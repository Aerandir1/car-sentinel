package fr.car.sentinel;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.content.res.AssetManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * Fait tourner le dashboard Python (acquisition + serveur sur le port 8082) au premier plan : la lecture
 * continue écran éteint ou appli en arrière-plan, comme le service systemd sur le PC.
 */
public class SentinelService extends Service {
    static final String ACTION_STOP = "fr.car.sentinel.STOP";
    private static final String CHANNEL = "sentinel";
    private static final String TAG = "CarSentinel";
    private static boolean pythonRunning;   // le Python ne s'arrête pas : un seul lancement par processus
    private PowerManager.WakeLock wakeLock;

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopForeground(STOP_FOREGROUND_REMOVE);
            stopSelf();
            System.exit(0);   // seul moyen d'arrêter les threads Python
            return START_NOT_STICKY;
        }
        startInForeground();
        if (wakeLock == null) {
            PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "car:acquisition");
            wakeLock.acquire();
        }
        startPython();
        return START_STICKY;
    }

    private void startInForeground() {
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Lecture OBD", NotificationManager.IMPORTANCE_LOW));
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class),
                PendingIntent.FLAG_IMMUTABLE);
        PendingIntent stop = PendingIntent.getService(this, 1,
                new Intent(this, SentinelService.class).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE);
        Notification n = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(R.drawable.ic_notif)
                .setContentTitle("Car Sentinel")
                .setContentText("Lecture de l'ELM327 et dashboard actifs")
                .setContentIntent(open)
                .addAction(new Notification.Action.Builder(null, "Quitter", stop).build())
                .setOngoing(true)
                .build();
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
        } else {
            startForeground(1, n);
        }
    }

    private synchronized void startPython() {
        if (pythonRunning) {
            return;
        }
        pythonRunning = true;
        UsbSerial.init(this);
        File web = new File(getFilesDir(), "web");
        try {
            copyAssets(getAssets(), "web", web);   // dashboard de cette version de l'appli
        } catch (IOException e) {
            Log.e(TAG, "copie du dashboard", e);
        }
        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(this));
        }
        Thread t = new Thread(() -> {
            try {
                Python.getInstance().getModule("android_main").callAttr("main",
                        getFilesDir().getAbsolutePath(), web.getAbsolutePath(), BuildConfig.CAR_VERSION,
                        BuildConfig.CAR_SERVER, Build.MANUFACTURER + " " + Build.MODEL);
            } catch (Throwable e) {
                Log.e(TAG, "le dashboard Python s'est arrêté", e);
            }
        }, "python-dashboard");
        t.setDaemon(true);
        t.start();
    }

    private static void copyAssets(AssetManager am, String path, File dest) throws IOException {
        String[] children = am.list(path);
        if (children != null && children.length > 0) {
            dest.mkdirs();
            for (String c : children) {
                copyAssets(am, path + "/" + c, new File(dest, c));
            }
            return;
        }
        try (InputStream in = am.open(path); OutputStream out = new FileOutputStream(dest)) {
            byte[] b = new byte[16384];
            int n;
            while ((n = in.read(b)) > 0) {
                out.write(b, 0, n);
            }
        }
    }

    @Override
    public void onDestroy() {
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
