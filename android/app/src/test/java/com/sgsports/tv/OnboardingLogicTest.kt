package com.sgsports.tv

import org.junit.Assert.*
import org.junit.Test

class OnboardingLogicTest {
 @Test fun onlyAnUnpairedBuildWithoutBuiltInBackendOnboards(){
  assertTrue(OnboardingLogic.shouldOnboard(paired=false,builtInBackend="",done=false,designMode=false))
  assertFalse("existing pairing bypasses",OnboardingLogic.shouldOnboard(true,"",false,false))
  assertFalse("configured installation bypasses",OnboardingLogic.shouldOnboard(false,"https://sports.example.com",false,false))
  assertFalse("finished or skipped once",OnboardingLogic.shouldOnboard(false,"",true,false))
  assertFalse("design preview",OnboardingLogic.shouldOnboard(false,"",false,true))
 }

 @Test fun aSelectedBackendStartsAtItsConnectionTest(){
  assertEquals(OnboardingStep.ADDRESS,OnboardingLogic.initial("").step)
  val ui=OnboardingLogic.initial("http://192.168.1.20:8099")
  assertEquals(OnboardingStep.TESTING,ui.step);assertTrue(ui.busy)
  assertEquals("http://192.168.1.20:8099/",ui.consoleUrl)
 }

 @Test fun theFlowMovesAddressTestPairWithRetryAndEdit(){
  val tested=OnboardingLogic.addressAccepted("http://nas.local:8099")
  assertEquals(OnboardingStep.TESTING,tested.step)
  val failed=OnboardingLogic.testFailed(tested)
  assertEquals(OnboardingStep.ADDRESS,failed.step);assertFalse(failed.busy);assertTrue(failed.error.contains("http://nas.local:8099"))
  assertEquals("retry keeps the address shown","http://nas.local:8099",failed.backend)
  val ok=OnboardingLogic.testPassed(tested)
  assertEquals(OnboardingStep.PAIR,ok.step);assertEquals("",ok.error)
  val rejected=OnboardingLogic.addressRejected(OnboardingUi(),"Use an http:// or https:// address.")
  assertEquals(OnboardingStep.ADDRESS,rejected.step);assertEquals("Use an http:// or https:// address.",rejected.error)
 }

 @Test fun backWalksTowardsTheAddressAndIsIgnoredWhileBusy(){
  val pair=OnboardingLogic.testPassed(OnboardingLogic.addressAccepted("http://nas.local:8099"))
  assertEquals(OnboardingStep.ADDRESS,OnboardingLogic.back(pair)!!.step)
  assertEquals(pair.copy(busy=true),OnboardingLogic.back(pair.copy(busy=true)))
  val cancelled=OnboardingLogic.back(OnboardingLogic.addressAccepted("http://nas.local:8099"))!!
  assertEquals(OnboardingStep.ADDRESS,cancelled.step);assertFalse(cancelled.busy);assertEquals("http://nas.local:8099",cancelled.backend)
  assertNull("leaving from the address step is the system's",OnboardingLogic.back(OnboardingUi()))
  assertNull(OnboardingLogic.back(pair.copy(step=OnboardingStep.DONE)))
 }

 @Test fun pairingFailuresAreHumanAndSecretFree(){
  for(status in listOf(0,400,401,403,404,409,429,500,418)){
   val m=OnboardingLogic.pairMessage(status)
   assertTrue(m.length>20);assertFalse(m.contains("token",true))
  }
  assertTrue(OnboardingLogic.pairMessage(401).contains("new code"))
  assertTrue(OnboardingLogic.pairMessage(0).contains("network"))
 }

 @Test fun theCodeKeepsSixDigitsOnly(){
  assertEquals("123456",OnboardingLogic.cleanCode(" 123 456 "))
  assertEquals("123456",OnboardingLogic.cleanCode("1234567890"))
  assertEquals("12",OnboardingLogic.cleanCode("a1-b2"))
  assertTrue(OnboardingLogic.codeLooksComplete("123456"));assertFalse(OnboardingLogic.codeLooksComplete("12345"))
 }
}
