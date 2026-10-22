package com.sgsports.tv

import android.content.Context

/** Studio and Living Room builds carry no fixtures; the Design flavor supplies the real ones. */
object DesignFixtures {
 fun state(context:Context):SgState? = null
 fun teamHub(teamId:String):HubFixture? = null
 fun feeds(mapper:GameMapper):List<Feed>? = null
 fun recordings():RecordingsSnapshot? = null
}
