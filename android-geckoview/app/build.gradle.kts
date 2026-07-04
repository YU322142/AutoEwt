plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

val geckoViewArtifact: String by project
val geckoViewVersion: String by project

android {
    namespace = "io.github.autoewt.gecko"
    compileSdk = 36

    defaultConfig {
        applicationId = "io.github.autoewt.gecko"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        buildConfig = true
        compose = true
    }
}

dependencies {
    implementation("org.mozilla.geckoview:$geckoViewArtifact:$geckoViewVersion")
    implementation("androidx.activity:activity:1.13.0")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation(platform("androidx.compose:compose-bom:2026.06.01"))
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
