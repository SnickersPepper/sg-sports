package com.sgsports.tv

import java.io.File
import java.util.concurrent.ConcurrentHashMap

/** How a [Mark] path or catalog identity maps to the backend's canonical artwork key. Pure logic, unit-tested without Android. */
object ArtworkKeys {
 const val PREFIX="art:"
 private val STEM=Regex("""^logos/((?:espn|league)_[a-z0-9_.-]+)\.png$""")
 private val CATALOG=Regex("""^(?:team|league)(?:\.[a-z0-9]+(?:-[a-z0-9]+)*){2,5}$""")
 private val SAFE=Regex("""^[a-z0-9][a-z0-9_.-]{2,110}$""")
 /** The key the backend serves for [path], or null when the path is bundled-only. Bundled stems and `art:<catalog id>` are the only accepted forms. */
 fun keyFor(path:String):String? {
  if(path.startsWith(PREFIX)){val id=path.removePrefix(PREFIX);return if(CATALOG.matches(id))id else null}
  return STEM.matchEntire(path)?.groupValues?.get(1)
 }
 /** A team's logo path: its own bundled one, else the backend artwork for its catalog ID. */
 fun forTeam(id:String,logo:String):String = logo.ifBlank{if(CATALOG.matches(id))PREFIX+id else ""}
 fun isSafe(key:String)=SAFE.matches(key)
}

/** Device-side last-good artwork: one file per key, written atomically, bounded by size and count (oldest written evicted first). */
class ArtworkDisk(private val dir:File,private val maxBytes:Long=24L*1024*1024,private val maxFiles:Int=1500) {
 companion object { const val MAX_ITEM=1_000_000 }
 init { dir.mkdirs() }
 private fun file(key:String)=File(dir,key+".img")
 fun read(key:String):ByteArray? = if(!ArtworkKeys.isSafe(key))null else file(key).takeIf{it.isFile}?.let{runCatching{it.readBytes()}.getOrNull()}
 fun modified(key:String):Long? = if(!ArtworkKeys.isSafe(key))null else file(key).takeIf{it.isFile}?.lastModified()
 fun write(key:String,bytes:ByteArray) {
  if(!ArtworkKeys.isSafe(key)||bytes.isEmpty()||bytes.size>MAX_ITEM)return
  val tmp=File(dir,"$key.tmp")
  runCatching{tmp.writeBytes(bytes);if(!tmp.renameTo(file(key))){file(key).delete();tmp.renameTo(file(key))}}.onFailure{tmp.delete()}
  trim()
 }
 fun clear(){dir.listFiles()?.forEach{it.delete()}}
 fun count()=dir.listFiles{f->f.name.endsWith(".img")}?.size?:0
 private fun trim() {
  val files=dir.listFiles{f->f.name.endsWith(".img")}?.sortedBy{it.lastModified()}?:return
  var total=files.sumOf{it.length()};var left=files.size
  for(f in files){if(total<=maxBytes&&left<=maxFiles)break;total-=f.length();left--;f.delete()}
 }
}

/**
 * Artwork by canonical key: the device copy is used while it is fresh; an older copy is refreshed from the backend and, if the backend
 * cannot be reached, still returned (last good). A key with nothing at all is not asked for again until [retryAfterMs] has passed, so a
 * page full of unavailable logos costs one request each, not one per redraw. [network] returns null for "not available".
 */
class ArtworkRepository(
 private val disk:ArtworkDisk,
 private val network:(String)->ByteArray?,
 private val clock:()->Long={System.currentTimeMillis()},
 private val maxAgeMs:Long=14L*24*3600*1000,
 private val retryAfterMs:Long=5L*60*1000,
) {
 private val missedAt=ConcurrentHashMap<String,Long>()
 fun load(key:String):ByteArray? {
  if(!ArtworkKeys.isSafe(key))return null
  val cached=disk.read(key)
  val written=disk.modified(key)
  if(cached!=null&&written!=null&&clock()-written<maxAgeMs)return cached
  val missed=missedAt[key]
  if(missed!=null&&clock()-missed<retryAfterMs)return cached
  val fresh=runCatching{network(key)}.getOrNull()
  if(fresh!=null&&fresh.isNotEmpty()&&fresh.size<=ArtworkDisk.MAX_ITEM){disk.write(key,fresh);missedAt.remove(key);return fresh}
  missedAt[key]=clock()
  return cached
 }
 /** Forget recent failures, e.g. after pairing or a backend change, so the next draw asks again. */
 fun retryNow(){missedAt.clear()}
}

/** Team-colour monogram used when no artwork is available: a stable colour and one or two initials derived from the name. */
object Monogram {
 private val SKIP=setOf("fc","afc","cf","sc","ac","cd","ud","the","de","la","el","of")
 fun initials(label:String):String {
  val words=label.trim().split(Regex("""[^\p{L}\p{N}]+""")).filter{it.isNotEmpty()}
  if(words.isEmpty())return "?"
  val significant=words.filter{it.lowercase() !in SKIP}.ifEmpty{words}
  return if(significant.size==1)significant[0].take(2).uppercase() else (significant[0].take(1)+significant[1].take(1)).uppercase()
 }
 /** A hue in [0,360) that depends only on the name, so the same team always gets the same colour. */
 fun hue(label:String):Float {
  var h=17
  for(c in label.trim().lowercase())h=h*31+c.code
  return ((h%360)+360)%360.toFloat()
 }
}
