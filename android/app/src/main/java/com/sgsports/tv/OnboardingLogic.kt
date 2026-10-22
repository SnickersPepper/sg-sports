package com.sgsports.tv

/** First-run flow on the TV: backend address -> connection test -> pairing -> success -> optional preferences. No administrator credential is ever typed here. */
enum class OnboardingStep { ADDRESS, TESTING, PAIR, DONE }

data class OnboardingUi(
 val step:OnboardingStep=OnboardingStep.ADDRESS,
 /** The normalised backend address once one was accepted; empty before that. */
 val backend:String="",
 /** Human-readable problem for the current step; empty when there is none. */
 val error:String="",
 /** A request is in flight: its actions are disabled. */
 val busy:Boolean=false
) {
 /** The backend's administration console (its root address). */
 val consoleUrl:String get()=if(backend.isEmpty())"" else BackendPolicy.adminUrl(backend)
}

object OnboardingLogic {
 const val KEY_DONE="onboarding_done"

 /**
  * Show onboarding only for a build with no built-in backend, on a TV that holds no pairing and never finished or skipped it.
  * A paired TV, a flavor with a configured default backend and design previews continue exactly as before.
  */
 fun shouldOnboard(paired:Boolean,builtInBackend:String,done:Boolean,designMode:Boolean)=!designMode&&!paired&&!done&&builtInBackend.isBlank()

 /** The step to start from: a backend already selected goes straight to its connection test. */
 fun initial(currentBackend:String)=if(currentBackend.isBlank())OnboardingUi() else OnboardingUi(OnboardingStep.TESTING,currentBackend,busy=true)

 fun addressAccepted(url:String)=OnboardingUi(OnboardingStep.TESTING,url,busy=true)
 fun addressRejected(previous:OnboardingUi,reason:String)=previous.copy(step=OnboardingStep.ADDRESS,error=reason,busy=false)
 fun testPassed(ui:OnboardingUi)=ui.copy(step=OnboardingStep.PAIR,error="",busy=false)
 fun testFailed(ui:OnboardingUi)=ui.copy(step=OnboardingStep.ADDRESS,busy=false,
  error="No SG Sports backend answered at ${ui.backend}. Check that the backend is running and the address and port are right, then try again.")
 fun pairing(ui:OnboardingUi)=ui.copy(busy=true,error="")

 /** What a pairing attempt's HTTP status means to a person (0 = the backend could not be reached). */
 fun pairMessage(status:Int)=when(status){
  0->"Could not reach the backend. Check your network, then try again."
  400,401,403,404,409->"That code was not accepted. Codes expire after five minutes: create a new code in the backend console and enter it again."
  429->"Too many attempts. Wait a minute, then enter a new code."
  in 500..599->"The backend had a problem. Try again in a moment."
  else->"Pairing did not work (error $status). Create a new code and try again."
 }

 /** Back cancels a connection test, then goes one step towards the address; it never abandons a paired result and waits out a pairing request. */
 fun back(ui:OnboardingUi):OnboardingUi?=when{
  ui.step==OnboardingStep.TESTING->ui.copy(step=OnboardingStep.ADDRESS,busy=false,error="")
  ui.busy->ui
  ui.step==OnboardingStep.PAIR->ui.copy(step=OnboardingStep.ADDRESS,error="")
  else->null
 }

 /** Six digits only; whitespace and separators typed with the code are ignored. */
 fun cleanCode(input:String):String=input.filter{it.isDigit()}.take(6)
 fun codeLooksComplete(code:String)=code.length==6
}
