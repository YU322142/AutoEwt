package io.github.autoewt.gecko;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

public class AutoEwtForegroundService extends Service {
    public static final String ACTION_START = "io.github.autoewt.gecko.action.START_FOREGROUND";
    public static final String ACTION_STOP = "io.github.autoewt.gecko.action.STOP_FOREGROUND";
    public static final String EXTRA_STATUS = "status";

    private static final String CHANNEL_ID = "autoewt_background";
    private static final int NOTIFICATION_ID = 3601;
    private boolean foregroundStarted = false;

    @Override
    public void onCreate() {
        super.onCreate();
        ensureNotificationChannel();
        startForegroundSafely(buildNotification("正在准备后台保活"));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_START : intent.getAction();
        if (ACTION_STOP.equals(action)) {
            if (foregroundStarted) {
                stopForeground(true);
            }
            stopSelf();
            return START_NOT_STICKY;
        }

        String status = intent == null ? "" : intent.getStringExtra(EXTRA_STATUS);
        Notification notification = buildNotification(status == null || status.trim().isEmpty()
                ? "自动化运行中，点击返回应用"
                : status);
        if (!startForegroundSafely(notification)) {
            stopSelf(startId);
            return START_NOT_STICKY;
        }
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private boolean startForegroundSafely(Notification notification) {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(
                        NOTIFICATION_ID,
                        notification,
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
                );
            } else {
                startForeground(NOTIFICATION_ID, notification);
            }
            foregroundStarted = true;
            return true;
        } catch (RuntimeException typedError) {
            try {
                startForeground(NOTIFICATION_ID, notification);
                foregroundStarted = true;
                return true;
            } catch (RuntimeException plainError) {
                foregroundStarted = false;
                return false;
            }
        }
    }

    private Notification buildNotification(String status) {
        Intent launchIntent = new Intent(this, MainActivity.class);
        launchIntent.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent contentIntent = PendingIntent.getActivity(
                this,
                3601,
                launchIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );

        Notification.Builder builder = new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_play)
                .setContentTitle("AutoEwt 正在后台保活")
                .setContentText(status)
                .setOngoing(true)
                .setShowWhen(false)
                .setContentIntent(contentIntent);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            builder.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE);
        }
        return builder.build();
    }

    private void ensureNotificationChannel() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "后台运行",
                NotificationManager.IMPORTANCE_LOW
        );
        channel.setDescription("自动化运行时保持前台服务通知，降低后台被系统清理的概率。");
        manager.createNotificationChannel(channel);
    }
}
