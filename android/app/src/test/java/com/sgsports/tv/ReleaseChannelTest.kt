package com.sgsports.tv

import org.junit.Assert.*
import org.junit.Test

class ReleaseChannelTest {
 @Test fun labelsEveryKnownChannel(){
  assertEquals("SG Sports 0.9.0 · STUDIO build",ReleaseChannel.label("0.9.0","studio"))
  assertEquals("SG Sports 0.9.0 · LIVING ROOM build",ReleaseChannel.label("0.9.0","livingroom"))
  assertEquals("SG Sports 0.9.0 · DESIGN build",ReleaseChannel.label("0.9.0","design"))
 }
 @Test fun unknownChannelIsNeverEchoed(){
  assertEquals("UNSPECIFIED",ReleaseChannel.badge("https://user:secret@host"))
  assertFalse(ReleaseChannel.label("1","x@y").contains("x@y"))
 }
 @Test fun mismatchOnlyWhenBackendReportsADifferentKnownChannel(){
  assertFalse(ReleaseChannel.mismatch("studio",null))
  assertFalse(ReleaseChannel.mismatch("studio","unspecified"))
  assertFalse(ReleaseChannel.mismatch("studio","studio"))
  assertTrue(ReleaseChannel.mismatch("studio","livingroom"))
 }
}
