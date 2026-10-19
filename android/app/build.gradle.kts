import java.io.ByteArrayOutputStream

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

// Commit du dépôt, enregistré avec chaque session (CAR_VERSION)
fun gitVersion(): String = try {
    val out = ByteArrayOutputStream()
    project.exec {
        commandLine("git", "-C", rootDir.parent, "describe", "--always", "--dirty")
        standardOutput = out
    }
    out.toString().trim()
} catch (e: Exception) { "android" }

// Serveur d'office de la tablette : SERVEUR de car.conf du PC qui compile (modifiable ensuite dans Réglages)
fun confServer(): String = rootDir.resolve("../car.conf").takeIf { it.isFile }?.readLines()
    ?.map { it.trim() }?.firstOrNull { it.startsWith("SERVEUR=") }
    ?.substringAfter("=")?.trim()?.trim('"', '\'') ?: ""

android {
    namespace = "fr.car.sentinel"
    compileSdk = 36

    defaultConfig {
        applicationId = "fr.car.sentinel"
        minSdk = 26
        targetSdk = 36
        versionCode = 4
        versionName = "2.0"
        buildConfigField("String", "CAR_VERSION", "\"${gitVersion()}\"")
        buildConfigField("String", "CAR_SERVER", "\"${confServer()}\"")
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }   // tablettes Samsung (arm64) et émulateur
    }

    buildFeatures { buildConfig = true }

    // Le dashboard (web/) est copié dans les assets : le service le recopie dans les fichiers de l'appli
    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("generated/car-assets"))

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
    into(layout.buildDirectory.dir("generated/car-assets/web"))
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
