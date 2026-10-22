package com.sgsports.tv

import org.junit.Assume
import java.io.File

/**
 * Test view of the bundled assets. The public tree bundles no third-party artwork and no personal roster (they live in `src/private/assets`
 * of the maintainer's tree); a test that needs them is skipped, not failed, where they are absent.
 */
object TestAssets {
 private fun dir(vararg candidates:String)=candidates.map{File(it)}.firstOrNull{it.isDirectory}
 val main:File=dir("src/main/assets","app/src/main/assets")!!
 val private:File?=dir("src/private/assets-art","app/src/private/assets-art")
 fun file(name:String):File=private?.let{File(it,name)}?.takeIf{it.exists()}?:File(main,name)
 fun hasPrivateSet()=private?.let{File(it,"favorites.json").exists()}==true
 fun assumePrivate()=Assume.assumeTrue("maintainer artwork/roster set is not part of this tree",hasPrivateSet())
 /** A bundled crest can only be checked where the artwork set exists. */
 fun crestOk(path:String)=!hasPrivateSet()||file(path).length()>0
}
