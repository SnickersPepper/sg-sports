package com.sgsports.tv

import org.junit.Assert.*
import org.junit.Test

private class MemoryStore(val values:MutableMap<String,String> = mutableMapOf()):PrefStore {
 var edits=0
 override fun get(key:String)=values[key]
 override fun edit(puts:Map<String,String>,removes:Set<String>){edits++;removes.forEach{values.remove(it)};values.putAll(puts)}
}

class BackendConfigTest {
 private val default="https://sports.default.example"

 private fun ok(input:String)=(BackendPolicy.normalize(input) as AddressResult.Ok).url
 private fun rejected(input:String)=BackendPolicy.normalize(input) is AddressResult.Rejected

 @Test fun normalizesAddresses(){
  assertEquals("https://sports.example.com",ok(" Sports.Example.com/ "))
  assertEquals("https://sports.example.com",ok("https://sports.example.com:443"))
  assertEquals("http://192.168.1.20:8099",ok("http://192.168.1.20:8099/"))
  assertEquals("http://sgsports.local",ok("http://sgsports.local:80"))
  assertEquals("http://nas",ok("http://nas"))
  assertEquals("https://sports.example.com:8443",ok("https://sports.example.com:8443"))
 }

 @Test fun rejectsUnsafeOrMalformedAddresses(){
  listOf("","  ","ftp://x.example","https://user:pass@sports.example.com","https://sports.example.com/path","https://sports.example.com/?k=v",
   "https://sports.example.com/#frag","https://","http://sports.example.com","http://8.8.8.8","http://172.32.0.1","http://[2001:db8::1]","https://bad host",
   "https://sports.example.com:70000","javascript:alert(1)").forEach{assertTrue("should reject '$it'",rejected(it))}
  assertEquals("http://172.16.5.4",ok("http://172.16.5.4"))
  assertEquals("http://[::1]:8099",ok("http://[::1]:8099"))
 }

 @Test fun defaultBackendIsUsedUntilAnotherIsChosen(){
  val s=MemoryStore()
  assertEquals(default,BackendPolicy.current(s,default))
  s.values[BackendPolicy.KEY_URL]="not a url"
  assertEquals(default,BackendPolicy.current(s,default)) // an unreadable saved value falls back, never crashes or forwards
 }

 @Test fun legacyInstalledTokenStaysPairedWithTheBuildDefault(){
  val s=MemoryStore(mutableMapOf("token" to "legacy-token","language" to "es"))
  assertEquals("legacy-token",BackendPolicy.token(s,default))
  // ... but never with any other backend
  s.values[BackendPolicy.KEY_URL]="https://other.example.com"
  assertEquals("",BackendPolicy.token(s,default))
 }

 @Test fun pairingIsBoundToTheBackendThatIssuedIt(){
  val s=MemoryStore()
  BackendPolicy.switchTo(s,default,"https://home.example.com")
  BackendPolicy.savePairing(s,default,"tok-home")
  assertEquals("tok-home",BackendPolicy.token(s,default))
  s.values[BackendPolicy.KEY_URL]="https://elsewhere.example.com" // even a direct write cannot make the token follow
  assertEquals("",BackendPolicy.token(s,default))
 }

 @Test fun switchingBackendDropsTheCredentialAtomicallyAndKeepsOtherSettings(){
  val s=MemoryStore(mutableMapOf("token" to "legacy-token","language" to "fr"))
  val r=BackendPolicy.switchTo(s,default,"http://192.168.1.20:8099") as SwitchResult.Switched
  assertEquals(default,r.previous);assertTrue(r.hadPairing);assertEquals("http://192.168.1.20:8099",r.url)
  assertEquals(1,s.edits)
  assertNull(s.values["token"]);assertNull(s.values[BackendPolicy.KEY_TOKEN_BACKEND])
  assertEquals("fr",s.values["language"])
  assertEquals("",BackendPolicy.token(s,default))
  // switching back to the build default clears the override and again needs a fresh pairing
  BackendPolicy.savePairing(s,default,"tok-lan")
  val back=BackendPolicy.switchTo(s,default,default) as SwitchResult.Switched
  assertTrue(back.hadPairing);assertNull(s.values[BackendPolicy.KEY_URL]);assertEquals("",BackendPolicy.token(s,default))
 }

 @Test fun reselectingTheCurrentBackendOrBadInputChangesNothing(){
  val s=MemoryStore(mutableMapOf("token" to "legacy-token"))
  assertTrue(BackendPolicy.switchTo(s,default,default) is SwitchResult.Unchanged)
  assertTrue(BackendPolicy.switchTo(s,default,"https://user:pw@evil.example") is SwitchResult.Rejected)
  assertEquals(0,s.edits);assertEquals("legacy-token",BackendPolicy.token(s,default))
 }

 @Test fun adminHandoffAddressCarriesNoCredential(){
  val url=BackendPolicy.adminUrl("https://sports.example.com/")
  assertEquals("https://sports.example.com/",url)
  assertFalse(url.contains("@")||url.contains("?")||url.contains("token",true))
 }

 @Test fun dnsOverridesAcceptOnlyHostToIpLiterals(){
  assertEquals(mapOf("sports.a.example" to "10.0.0.1"),BackendPolicy.parseDnsOverrides("sports.a.example=10.0.0.1"))
  assertEquals(emptyMap<String,String>(),BackendPolicy.parseDnsOverrides(""))
  assertEquals(emptyMap<String,String>(),BackendPolicy.parseDnsOverrides("x.example=evil.example;=1.2.3.4;noequals"))
 }

 @Test fun adminCheckSaysWhetherTheSetupPageIsActuallyServed(){
  assertEquals(AdminCheck.AVAILABLE,BackendPolicy.adminCheckFor(200))
  assertEquals(AdminCheck.NOT_SERVED,BackendPolicy.adminCheckFor(404))
  assertEquals(AdminCheck.UNREACHABLE,BackendPolicy.adminCheckFor(0))
  assertEquals(AdminCheck.UNREACHABLE,BackendPolicy.adminCheckFor(502))
  assertTrue(AdminCheck.NOT_SERVED.note.contains("older version"))
 }
}
