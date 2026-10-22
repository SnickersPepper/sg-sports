package com.sgsports.tv

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.nio.file.Files

/** Device-side artwork: key mapping, last-good disk cache, offline behaviour, retry throttling and the monogram fallback. */
class ArtworkStoreTest {
 private fun tempDir():File=Files.createTempDirectory("artwork-test").toFile().also{it.deleteOnExit()}
 private val png=ByteArray(64){it.toByte()}

 @Test fun `bundled stems and catalog ids map to backend keys and nothing else does`(){
  assertEquals("espn_football_nfl_12",ArtworkKeys.keyFor("logos/espn_football_nfl_12.png"))
  assertEquals("league_soccer_eng_1",ArtworkKeys.keyFor("logos/league_soccer_eng_1.png"))
  assertEquals("team.football.men.nfl.kansas-city-chiefs",ArtworkKeys.keyFor("art:team.football.men.nfl.kansas-city-chiefs"))
  for(bad in listOf("","logos/team_bcn.png","logos/../espn_x_1.png","art:../../etc/passwd","art:team.x","logos/espn_football_nfl_12.png/../x","banners/favorite-0-1.jpg","lady_vols","https://evil.example/x.png","art:"))assertNull(bad,ArtworkKeys.keyFor(bad))
 }

 @Test fun `team logo prefers its own bundled path and falls back to the catalog identity`(){
  assertEquals("logos/team_bcn.png",ArtworkKeys.forTeam("team.soccer.men.la-liga.fc-barcelona","logos/team_bcn.png"))
  assertEquals("art:team.soccer.men.la-liga.fc-barcelona",ArtworkKeys.forTeam("team.soccer.men.la-liga.fc-barcelona",""))
  assertEquals("",ArtworkKeys.forTeam("not an id",""))
 }

 @Test fun `fetched art is kept on the device and served without the network afterwards`(){
  var calls=0
  val repo=ArtworkRepository(ArtworkDisk(tempDir()),{calls++;png})
  assertArrayEquals(png,repo.load("espn_football_nfl_12"))
  assertArrayEquals(png,repo.load("espn_football_nfl_12"))
  assertEquals(1,calls)
 }

 @Test fun `last good art survives an unreachable backend even when it is stale`(){
  val dir=tempDir();val disk=ArtworkDisk(dir);var now=1_000_000_000L;var online=true;var calls=0
  val repo=ArtworkRepository(disk,{calls++;if(online)png else throw java.io.IOException("offline")},{now},maxAgeMs=1000,retryAfterMs=500)
  assertArrayEquals(png,repo.load("espn_football_nfl_12"))
  File(dir,"espn_football_nfl_12.img").setLastModified(now-5000)
  online=false
  assertArrayEquals(png,repo.load("espn_football_nfl_12"))   // stale, backend down: the old copy is still shown
  val during=calls
  assertArrayEquals(png,repo.load("espn_football_nfl_12"))
  assertEquals("no repeat request inside the retry window",during,calls)
  now+=600;online=true
  assertArrayEquals(png,repo.load("espn_football_nfl_12"))
  assertEquals(during+1,calls)
 }

 @Test fun `unavailable art is requested once then throttled and then retried`(){
  var now=0L;var calls=0
  val repo=ArtworkRepository(ArtworkDisk(tempDir()),{calls++;null},{now},retryAfterMs=1000)
  repeat(5){assertNull(repo.load("espn_football_nfl_77"))}
  assertEquals(1,calls)
  now+=1001;assertNull(repo.load("espn_football_nfl_77"));assertEquals(2,calls)
  repo.retryNow();assertNull(repo.load("espn_football_nfl_77"));assertEquals(3,calls)
 }

 @Test fun `a failing or oversized response never breaks loading and is not stored`(){
  val disk=ArtworkDisk(tempDir())
  assertNull(ArtworkRepository(disk,{throw IllegalStateException("boom")}).load("espn_football_nfl_1"))
  assertNull(ArtworkRepository(disk,{ByteArray(ArtworkDisk.MAX_ITEM+1)}).load("espn_football_nfl_2"))
  assertNull(ArtworkRepository(disk,{ByteArray(0)}).load("espn_football_nfl_3"))
  assertEquals(0,disk.count())
 }

 @Test fun `unsafe keys never touch the file system or the network`(){
  var calls=0
  val repo=ArtworkRepository(ArtworkDisk(tempDir()),{calls++;png})
  for(bad in listOf("../x","a/b","A","","x".repeat(200)))assertNull(bad,repo.load(bad))
  assertEquals(0,calls)
 }

 @Test fun `the device cache stays within its file limit by dropping the oldest`(){
  val dir=tempDir();val disk=ArtworkDisk(dir,maxFiles=2)
  disk.write("espn_a_1",png);File(dir,"espn_a_1.img").setLastModified(1000)
  disk.write("espn_a_2",png);File(dir,"espn_a_2.img").setLastModified(2000)
  disk.write("espn_a_3",png)
  assertEquals(2,disk.count());assertNull(disk.read("espn_a_1"));assertNotNull(disk.read("espn_a_3"))
 }

 @Test fun `monograms are stable readable and never empty`(){
  assertEquals("BA",Monogram.initials("FC Barcelona"))
  assertEquals("KC",Monogram.initials("Kansas City Chiefs"))
  assertEquals("TE",Monogram.initials("Tennessee"))
  assertEquals("?",Monogram.initials("  "))
  assertEquals("ES",Monogram.initials("Español"))
  assertEquals(Monogram.hue("Kansas City Chiefs"),Monogram.hue(" kansas city chiefs "),0f)
  assertTrue(Monogram.hue("x") in 0f..360f)
  assertNotEquals(Monogram.hue("Kansas City Chiefs"),Monogram.hue("Houston Astros"))
 }
}
