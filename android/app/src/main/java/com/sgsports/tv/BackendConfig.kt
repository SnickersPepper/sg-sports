package com.sgsports.tv

import android.content.SharedPreferences
import okhttp3.Dns
import java.net.InetAddress
import java.net.URI

/** Minimal key/value seam so the backend policy below is unit-testable without Android. */
interface PrefStore {
 fun get(key:String):String?
 /** One atomic change: every put and remove lands together. */
 fun edit(puts:Map<String,String>,removes:Set<String>)
}

class SharedPrefStore(private val prefs:SharedPreferences):PrefStore {
 override fun get(key:String):String?=prefs.getString(key,null)
 override fun edit(puts:Map<String,String>,removes:Set<String>){
  val e=prefs.edit();removes.forEach{e.remove(it)};puts.forEach{(k,v)->e.putString(k,v)};e.apply()
 }
}

sealed class AddressResult {
 data class Ok(val url:String):AddressResult()
 data class Rejected(val reason:String):AddressResult()
}

sealed class SwitchResult {
 data class Rejected(val reason:String):SwitchResult()
 /** The address is the one already in use: nothing was changed and the pairing is kept. */
 data class Unchanged(val url:String):SwitchResult()
 /** The backend changed. The old pairing was dropped locally and was not and cannot be sent to [url]. */
 data class Switched(val url:String,val previous:String,val hadPairing:Boolean):SwitchResult()
}

/**
 * Runtime backend selection and the pairing credential bound to it.
 *
 * The build supplies only a default address. A user-chosen address is stored on the device; the bearer token is stored
 * together with the address it was issued by and is never returned for any other address, so a backend change can
 * never forward or reuse a credential. A token saved before this binding existed is treated as issued by the build default.
 */
object BackendPolicy {
 const val KEY_URL="backend_url"
 const val KEY_TOKEN="token"
 const val KEY_TOKEN_BACKEND="token_backend"

 private val IPV4=Regex("""^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$""")
 private val LAN_SUFFIXES=listOf(".local",".lan",".home.arpa",".internal",".localdomain")

 fun normalize(input:String):AddressResult {
  var text=input.trim()
  if(text.isEmpty())return AddressResult.Rejected("Enter the address of your SG Sports backend.")
  if(!text.contains("://"))text="https://$text"
  val uri=try{URI(text)}catch(e:Exception){return AddressResult.Rejected("That is not a valid address.")}
  val scheme=uri.scheme?.lowercase()
  if(scheme!="https"&&scheme!="http")return AddressResult.Rejected("Use an http:// or https:// address.")
  if(uri.userInfo!=null)return AddressResult.Rejected("Do not put a user name or password in the address.")
  if(uri.rawQuery!=null||uri.rawFragment!=null)return AddressResult.Rejected("Leave out anything after the address (?, #).")
  if(!(uri.rawPath.isNullOrEmpty()||uri.rawPath=="/"))return AddressResult.Rejected("Use the backend's base address without a path.")
  val host=uri.host?.lowercase()?.takeIf{it.isNotEmpty()}?:return AddressResult.Rejected("That address has no host name.")
  if(!Regex("""^(\[[0-9a-f:.]+]|[a-z0-9]([a-z0-9.-]*[a-z0-9])?)$""").matches(host))return AddressResult.Rejected("That host name is not valid.")
  val port=uri.port
  if(port!=-1&&port !in 1..65535)return AddressResult.Rejected("That port is not valid.")
  if(scheme=="http"&&!isHomeNetworkHost(host))return AddressResult.Rejected("Outside your home network, use https:// so the pairing stays private.")
  val defaultPort=if(scheme=="https")443 else 80
  return AddressResult.Ok(scheme+"://"+host+(if(port==-1||port==defaultPort)"" else ":$port"))
 }

 /** Hosts where unencrypted http is acceptable: loopback, private/link-local ranges, mDNS/LAN names and single-label names. */
 fun isHomeNetworkHost(host:String):Boolean {
  val h=host.lowercase()
  if(h=="localhost"||LAN_SUFFIXES.any{h.endsWith(it)})return true
  if(h.startsWith("[")){val v6=h.trim('[',']');return v6=="::1"||v6.startsWith("fe80:")||v6.startsWith("fc")||v6.startsWith("fd")}
  val m=IPV4.matchEntire(h)
  if(m!=null){
   val o=m.groupValues.drop(1).map{it.toInt()}
   if(o.any{it>255})return false
   return o[0]==10||o[0]==127||(o[0]==172&&o[1] in 16..31)||(o[0]==192&&o[1]==168)||(o[0]==169&&o[1]==254)
  }
  return !h.contains('.')
 }

 fun current(store:PrefStore,default:String):String {
  val saved=store.get(KEY_URL)
  return (saved?.let{(normalize(it) as? AddressResult.Ok)?.url})?:default
 }

 /** The bearer token, only if it was issued by the backend currently selected. */
 fun token(store:PrefStore,default:String):String {
  val token=store.get(KEY_TOKEN).orEmpty()
  if(token.isEmpty())return ""
  val issuer=store.get(KEY_TOKEN_BACKEND)?:default
  return if(issuer==current(store,default))token else ""
 }

 fun savePairing(store:PrefStore,default:String,token:String)=
  store.edit(mapOf(KEY_TOKEN to token,KEY_TOKEN_BACKEND to current(store,default)),emptySet())

 fun forgetPairing(store:PrefStore)=store.edit(emptyMap(),setOf(KEY_TOKEN,KEY_TOKEN_BACKEND))

 /** Deliberate re-pair: select another backend and drop the old credential in one step. */
 fun switchTo(store:PrefStore,default:String,input:String):SwitchResult {
  val url=when(val r=normalize(input)){is AddressResult.Rejected->return SwitchResult.Rejected(r.reason);is AddressResult.Ok->r.url}
  val previous=current(store,default)
  if(url==previous)return SwitchResult.Unchanged(url)
  val hadPairing=token(store,default).isNotEmpty()
  val removes=mutableSetOf(KEY_TOKEN,KEY_TOKEN_BACKEND)
  val puts=mutableMapOf<String,String>()
  if(url==default)removes.add(KEY_URL) else puts[KEY_URL]=url
  store.edit(puts,removes)
  return SwitchResult.Switched(url,previous,hadPairing)
 }

 /** The only address handed to a phone or computer for administration: the backend root, its non-secret administration console (the older /setup address redirects there). */
 fun adminUrl(base:String)=base.trimEnd('/')+"/"

 /** What a plain GET of [adminUrl] answered (0 = no answer). An older backend that predates the setup page answers 404; saying so beats presenting a dead link as working. */
 fun adminCheckFor(status:Int)=when{
  status in 200..299->AdminCheck.AVAILABLE
  status==404||status==410->AdminCheck.NOT_SERVED
  else->AdminCheck.UNREACHABLE
 }

 /** "host=ip;host=ip" build-time name overrides for the flavor's own default hostnames. IP literals only. */
 fun parseDnsOverrides(spec:String):Map<String,String> {
  val ip=Regex("""^([0-9]{1,3}(\.[0-9]{1,3}){3}|[0-9a-f:]+)$""",RegexOption.IGNORE_CASE)
  val name=Regex("""^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$""")
  return spec.split(';').mapNotNull{entry->
   val parts=entry.split('=');if(parts.size!=2)return@mapNotNull null
   val host=parts[0].trim().lowercase();val address=parts[1].trim()
   if(name.matches(host)&&ip.matches(address))host to address else null
  }.toMap()
 }
}

/** Android-side convenience over [BackendPolicy] with this build's default address. */
object Backend {
 @JvmStatic fun base(prefs:SharedPreferences):String=BackendPolicy.current(SharedPrefStore(prefs),BuildConfig.SERVER_URL)
 @JvmStatic fun token(prefs:SharedPreferences):String=BackendPolicy.token(SharedPrefStore(prefs),BuildConfig.SERVER_URL)
 @JvmStatic fun savePairing(prefs:SharedPreferences,token:String)=BackendPolicy.savePairing(SharedPrefStore(prefs),BuildConfig.SERVER_URL,token)
 @JvmStatic fun forgetPairing(prefs:SharedPreferences)=BackendPolicy.forgetPairing(SharedPrefStore(prefs))
 @JvmStatic fun dns():Dns {
  val overrides=BackendPolicy.parseDnsOverrides(BuildConfig.DNS_OVERRIDES)
  return object:Dns { override fun lookup(hostname:String):List<InetAddress> = overrides[hostname.lowercase()]?.let{listOf(InetAddress.getByName(it))}?:Dns.SYSTEM.lookup(hostname) }
 }
}

/** Whether the displayed administration address was confirmed to serve the setup page. */
enum class AdminCheck(val note:String) {
 UNKNOWN(""),
 CHECKING("Checking that this address answers…"),
 AVAILABLE("This backend is serving its administration console."),
 NOT_SERVED("This backend did not answer with its administration console. It may be an older version; update the backend, then open this address."),
 UNREACHABLE("Could not check this address from the TV. Open it on a device in your home network.")
}
