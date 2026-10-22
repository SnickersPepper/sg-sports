package com.sgsports.tv

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Process-wide access to backend-served team artwork. The activity installs a credentials provider (backend address + pairing token);
 * nothing here knows a server address. Requests are bounded (four at a time, short timeouts) and every failure is a plain "no logo".
 */
object ArtworkRuntime {
 @Volatile private var repo:ArtworkRepository?=null
 @Volatile private var credentials:()->Pair<String,String> = {"" to ""}
 @Volatile private var lastCredentials:Pair<String,String>?=null
 private val gate=Semaphore(4)
 private val http by lazy{OkHttpClient.Builder().dns(Backend.dns()).connectTimeout(6,TimeUnit.SECONDS).readTimeout(10,TimeUnit.SECONDS).callTimeout(15,TimeUnit.SECONDS).build()}

 fun install(context:Context,provider:()->Pair<String,String>) {
  credentials=provider
  if(repo==null)repo=ArtworkRepository(ArtworkDisk(File(context.applicationContext.filesDir,"artwork")),::fetch)
 }

 /** Image bytes for a canonical key, from the device copy or the backend; null when unavailable. */
 suspend fun load(key:String):ByteArray? {
  val r=repo?:return null
  val now=credentials()
  if(now!=lastCredentials){lastCredentials=now;r.retryNow()}   // a new pairing or backend gets a fresh chance
  return gate.withPermit{withContext(Dispatchers.IO){r.load(key)}}
 }

 private fun fetch(key:String):ByteArray? {
  val (base,token)=credentials()
  if(base.isBlank()||token.isBlank())return null
  val request=Request.Builder().url("${base.trimEnd('/')}/tv/artwork/$key").header("Authorization","Bearer $token").build()
  http.newCall(request).execute().use{response->
   val body=response.body?:return null
   if(!response.isSuccessful||body.contentLength()>ArtworkDisk.MAX_ITEM)return null
   val out=java.io.ByteArrayOutputStream();val buffer=ByteArray(8192)
   body.byteStream().use{stream->
    while(true){val n=stream.read(buffer);if(n<0)break;out.write(buffer,0,n);if(out.size()>ArtworkDisk.MAX_ITEM)return null}
   }
   return out.toByteArray()
  }
 }
}
