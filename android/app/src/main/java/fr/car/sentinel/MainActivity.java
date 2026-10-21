package fr.car.sentinel;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.Settings;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.window.OnBackInvokedDispatcher;

/** Le dashboard en plein écran, servi par le Python du service (comme dans le navigateur du PC). */
public class MainActivity extends Activity {
    // Même adresse que sur le PC : le dashboard y range ses réglages (localStorage) et y redirige 127.0.0.1
    static final String URL = "http://car-sentinel.localhost:8080/";
    private final Handler handler = new Handler(Looper.getMainLooper());
    private WebView web;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        // Notifications (service) et localisation (GPS des tronçons) d'un coup, puis la batterie
        java.util.List<String> ask = new java.util.ArrayList<>();
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            ask.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED
                && !getPreferences(MODE_PRIVATE).getBoolean("gps_demande", false)) {
            getPreferences(MODE_PRIVATE).edit().putBoolean("gps_demande", true).apply();
            ask.add(Manifest.permission.ACCESS_FINE_LOCATION);
            ask.add(Manifest.permission.ACCESS_COARSE_LOCATION);
        }
        if (!ask.isEmpty()) {
            requestPermissions(ask.toArray(new String[0]), 1);
        } else {
            askBatteryExemption();
        }
        startForegroundService(new Intent(this, SentinelService.class));

        web = new WebView(this);
        web.setBackgroundColor(getColor(R.color.fond));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        s.setTextZoom(100);   // tailles de la page telles quelles : la taille de police système déborderait des jauges
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                // Serveur Python pas encore prêt (quelques secondes au premier lancement) : nouvel essai
                if (req.isForMainFrame()) {
                    handler.postDelayed(() -> view.loadUrl(URL), 1000);
                }
            }
        });
        setContentView(web);
        hideBars();
        if (Build.VERSION.SDK_INT >= 33) {   // Android 16 n'appelle plus onBackPressed (retour prédictif)
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                    OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::goBack);
        }
        web.loadUrl(URL);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED) {
            startForegroundService(new Intent(this, SentinelService.class));   // le service prend le GPS
        }
        askBatteryExemption();   // après la fenêtre des autorisations, pour ne pas les empiler
    }

    /**
     * Sort l'appli de l'économie de batterie (Samsung l'endort sinon, service ou pas) : la lecture et l'envoi au
     * serveur continuent en arrière-plan. Demandé une seule fois ; ensuite, Paramètres → Applications → Batterie.
     */
    private void askBatteryExemption() {
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        android.content.SharedPreferences prefs = getPreferences(MODE_PRIVATE);
        if (pm.isIgnoringBatteryOptimizations(getPackageName()) || prefs.getBoolean("batterie_demandee", false)) {
            return;
        }
        prefs.edit().putBoolean("batterie_demandee", true).apply();
        try {
            startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:" + getPackageName())));
        } catch (android.content.ActivityNotFoundException e) {
            // tablette sans cette fenêtre : réglage à faire à la main
        }
    }

    private void hideBars() {
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsetsController c = getWindow().getInsetsController();
            if (c != null) {
                c.hide(WindowInsets.Type.systemBars());
                c.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        } else {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN
                    | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
        }
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            hideBars();
        }
    }

    @Override
    public void onBackPressed() {   // Android 12 et avant
        goBack();
    }

    private void goBack() {
        // La page ferme d'abord la courbe ouverte, ou revient au tableau de bord depuis un autre onglet (carBack)
        web.evaluateJavascript("window.carBack ? carBack() : false", r -> {
            if (!"true".equals(r)) {
                moveTaskToBack(true);   // la lecture continue en arrière-plan (notification « Quitter » pour l'arrêter)
            }
        });
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        web.destroy();
        super.onDestroy();
    }
}
