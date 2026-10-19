package fr.car.sentinel;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.hardware.usb.UsbConstants;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbManager;
import android.os.Build;

import com.hoho.android.usbserial.driver.CdcAcmSerialDriver;
import com.hoho.android.usbserial.driver.UsbSerialDriver;
import com.hoho.android.usbserial.driver.UsbSerialPort;
import com.hoho.android.usbserial.driver.UsbSerialProber;

import java.io.IOException;
import java.util.List;

/**
 * Port série de la clé ELM327 USB, pour le Python (car_usb/protocol_androidusb.py) : Android ne donne pas
 * de /dev/ttyACM0 aux applis, il faut passer par l'API USB host (usb-serial-for-android).
 * Un thread lit la clé en continu et garde ce qui arrive en mémoire (in_waiting de pyserial).
 */
public final class UsbSerial {
    static final String ACTION_PERMISSION = "fr.car.sentinel.USB_PERMISSION";

    private static Context context;
    private static final Object lock = new Object();
    private static UsbSerialPort port;
    private static UsbDeviceConnection connection;
    private static byte[] buf = new byte[16384];
    private static int len;
    private static boolean lost;            // clé débranchée pendant la lecture
    private static int askedFor = -1;       // appareil dont l'autorisation a déjà été demandée
    private static volatile String name;

    private UsbSerial() {}

    static void init(Context ctx) {
        context = ctx.getApplicationContext();
    }

    /** Nom de la clé ouverte (affiché par le dashboard), ou null. */
    public static String name() {
        return name;
    }

    private static UsbSerialDriver findDriver(UsbManager manager) {
        List<UsbSerialDriver> drivers = UsbSerialProber.getDefaultProber().findAllDrivers(manager);
        if (!drivers.isEmpty()) {
            return drivers.get(0);
        }
        // Clé USB native inconnue de la table : toute interface CDC (ttyACM sur le PC) est essayée
        for (UsbDevice d : manager.getDeviceList().values()) {
            for (int i = 0; i < d.getInterfaceCount(); i++) {
                if (d.getInterface(i).getInterfaceClass() == UsbConstants.USB_CLASS_COMM) {
                    return new CdcAcmSerialDriver(d);
                }
            }
        }
        return null;
    }

    public static void open(int baud) throws IOException {
        synchronized (lock) {
            if (port != null) {
                throw new IOException("port déjà ouvert");
            }
            UsbManager manager = (UsbManager) context.getSystemService(Context.USB_SERVICE);
            UsbSerialDriver driver = findDriver(manager);
            if (driver == null) {
                throw new IOException("aucune clé ELM327 USB branchée");
            }
            UsbDevice device = driver.getDevice();
            if (!manager.hasPermission(device)) {
                if (askedFor != device.getDeviceId()) {   // une seule fenêtre par branchement
                    askedFor = device.getDeviceId();
                    Intent intent = new Intent(ACTION_PERMISSION).setPackage(context.getPackageName());
                    int flags = Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0;
                    manager.requestPermission(device, PendingIntent.getBroadcast(context, 0, intent, flags));
                }
                throw new IOException("autorisation USB demandée (accepter sur l'écran)");
            }
            UsbDeviceConnection conn = manager.openDevice(device);
            if (conn == null) {
                throw new IOException("impossible d'ouvrir la clé USB");
            }
            UsbSerialPort p = driver.getPorts().get(0);
            try {
                p.open(conn);
                p.setParameters(baud > 0 ? baud : 38400, 8, UsbSerialPort.STOPBITS_1, UsbSerialPort.PARITY_NONE);
                try {
                    p.setDTR(true);
                    p.setRTS(true);
                } catch (IOException | UnsupportedOperationException ignored) {
                    // lignes de contrôle absentes sur certaines puces
                }
            } catch (IOException e) {
                try { p.close(); } catch (IOException ignored) {}
                conn.close();
                throw e;
            }
            port = p;
            connection = conn;
            len = 0;
            lost = false;
            String product = device.getProductName();
            name = product != null ? product : String.format("USB %04x:%04x", device.getVendorId(), device.getProductId());
            Thread reader = new Thread(() -> readLoop(p), "elm327-usb");
            reader.setDaemon(true);
            reader.start();
        }
    }

    private static void readLoop(UsbSerialPort p) {
        byte[] tmp = new byte[Math.max(64, p.getReadEndpoint().getMaxPacketSize())];
        while (true) {
            int n;
            try {
                n = p.read(tmp, 200);
            } catch (IOException e) {
                synchronized (lock) {
                    if (port == p) {
                        lost = true;
                        lock.notifyAll();
                    }
                }
                return;
            }
            synchronized (lock) {
                if (port != p) {
                    return;   // fermé
                }
                if (n > 0) {
                    if (len + n > buf.length) {
                        byte[] bigger = new byte[Math.max(buf.length * 2, len + n)];
                        System.arraycopy(buf, 0, bigger, 0, len);
                        buf = bigger;
                    }
                    System.arraycopy(tmp, 0, buf, len, n);
                    len += n;
                    lock.notifyAll();
                }
            }
        }
    }

    /** Jusqu'à max octets ; attend au plus timeoutMs qu'il en arrive (tableau vide sinon). */
    public static byte[] read(int max, int timeoutMs) throws IOException {
        long end = System.currentTimeMillis() + timeoutMs;
        synchronized (lock) {
            while (len == 0) {
                if (port == null || lost) {
                    throw new IOException("clé USB débranchée");
                }
                long wait = end - System.currentTimeMillis();
                if (wait <= 0) {
                    return new byte[0];
                }
                try {
                    lock.wait(wait);
                } catch (InterruptedException e) {
                    return new byte[0];
                }
            }
            int n = Math.min(max, len);
            byte[] out = new byte[n];
            System.arraycopy(buf, 0, out, 0, n);
            System.arraycopy(buf, n, buf, 0, len - n);
            len -= n;
            return out;
        }
    }

    public static void write(byte[] data, int timeoutMs) throws IOException {
        UsbSerialPort p;
        synchronized (lock) {
            if (port == null || lost) {
                throw new IOException("clé USB débranchée");
            }
            p = port;
        }
        p.write(data, timeoutMs);
    }

    public static int available() {
        synchronized (lock) {
            return len;
        }
    }

    public static void clearInput() {
        synchronized (lock) {
            len = 0;
        }
    }

    public static void setBaudRate(int baud) throws IOException {
        UsbSerialPort p;
        synchronized (lock) {
            p = port;
        }
        if (p != null && baud > 0) {
            p.setParameters(baud, 8, UsbSerialPort.STOPBITS_1, UsbSerialPort.PARITY_NONE);
        }
    }

    public static void close() {
        UsbSerialPort p;
        UsbDeviceConnection c;
        synchronized (lock) {
            p = port;
            c = connection;
            port = null;
            connection = null;
            len = 0;
            name = null;
            lock.notifyAll();
        }
        if (p != null) {
            try { p.close(); } catch (IOException ignored) {}
        }
        if (c != null) {
            c.close();
        }
    }
}
