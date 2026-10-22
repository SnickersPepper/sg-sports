package com.sgsports.tv

/** Release-channel labelling so a reviewer can always tell which build (design, studio or living room) is on screen. */
object ReleaseChannel {
 private val known=mapOf("design" to "DESIGN","studio" to "STUDIO","livingroom" to "LIVING ROOM","release" to "RELEASE")
 /** Upper-case badge for a channel id; any unknown id is shown as UNSPECIFIED rather than echoed. */
 fun badge(channel:String):String=known[channel]?:"UNSPECIFIED"
 /** The single diagnostics line shown in Settings and usable in logs: version and channel only, no addresses or identities. */
 fun label(version:String,channel:String):String="SG Sports $version · ${badge(channel)} build"
 /** True when the backend reports a different channel than this app (a studio app pointed at a living-room backend, or the reverse). */
 fun mismatch(appChannel:String,backendChannel:String?):Boolean=backendChannel!=null&&backendChannel!="unspecified"&&backendChannel!=appChannel
}
