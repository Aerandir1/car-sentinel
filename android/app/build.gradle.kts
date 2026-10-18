import java.io.ByteArrayOutputStream

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

// Commit du dépôt, enregistré avec chaque session (FIESTA_VERSION, comme dans Docker)
fun gitVersion(): String = try {
    val out = ByteArrayOutputStream()
    project.exec {
        commandLine("git", "-C", rootDir.parent, "describe", "--always", "--dirty")
        standardOutput = out
    }
    out.toString().trim()
} catch (e: Exception) { "android" }

// Serveur d'office de la tablette : SERVEUR de fiesta.conf du PC qui compile (modifiable ensuite dans Paramètres)
fun confServer(): String = rootDir.resolve("../fiesta.conf").takeIf { it.isFile }?.readLines()
    ?.map { it.trim() }?.firstOrNull { it.startsWith("SERVEUR=") }
    ?.substringAfter("=")?.trim()?.trim('"', '\'') ?: ""

android {
    namespace = "fr.fiesta.sentinel"
    compileSdk = 36

    defaultConfig {
        applicationId = "fr.fiesta.sentinel"
        minSdk = 26
        targetSdk = 36
        versionCode = 2
        versionName = "1.1"
        buildConfigField("String", "FIESTA_VERSION", "\"${gitVersion()}\"")
        buildConfigField("String", "FIESTA_SERVER", "\"${confServer()}\"")
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }   // tablettes Samsung (arm64) et émulateur
    }

    buildFeatures { buildConfig = true }

    // Le dashboard (web/) est copié dans les assets : le service le recopie dans les fichiers de l'appli
    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("generated/fiesta-assets"))

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("debug")   // APK installable tel quel (usage perso)
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

val copyWeb by tasks.registering(Sync::class) {
    from(rootDir.resolve("../web"))
    into(layout.buildDirectory.dir("generated/fiesta-assets/web"))
}
tasks.named("preBuild") { dependsOn(copyWeb) }

chaquopy {
    defaultConfig {
        version = "3.14"
        pip {
            install("obd==0.7.3")
            install("pyserial==3.5")
        }
    }
    sourceSets {
        getByName("main") {
            srcDir("../../src")   // le code Python du PC, tel quel
        }
    }
}

dependencies {
    implementation("com.github.mik3y:usb-serial-for-android:3.11.0")
}
