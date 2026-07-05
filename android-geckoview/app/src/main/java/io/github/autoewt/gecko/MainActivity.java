package io.github.autoewt.gecko;

import android.Manifest;
import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.SharedPreferences;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.drawable.Drawable;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.os.SystemClock;
import android.text.InputType;
import android.util.Log;
import android.view.InputDevice;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.TextView;

import androidx.activity.ComponentActivity;

import org.json.JSONException;
import org.json.JSONArray;
import org.json.JSONObject;
import org.mozilla.geckoview.AllowOrDeny;
import org.mozilla.geckoview.GeckoResult;
import org.mozilla.geckoview.GeckoRuntime;
import org.mozilla.geckoview.GeckoRuntimeSettings;
import org.mozilla.geckoview.GeckoSession;
import org.mozilla.geckoview.GeckoSessionSettings;
import org.mozilla.geckoview.GeckoView;
import org.mozilla.geckoview.WebExtension;

import java.text.SimpleDateFormat;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Date;
import java.util.Locale;

public class MainActivity extends ComponentActivity implements AutoEwtUiController {
    private static final String PREFS_NAME = "autoewt_gecko";
    private static final String KEY_LAST_URL = "last_url";
    private static final String KEY_USERNAME = "username";
    private static final String KEY_PASSWORD = "password";
    private static final String KEY_LIST_URL = "list_url";
    private static final String KEY_LIST_URL_TITLE = "list_url_title";
    private static final String KEY_MODE = "mode";
    private static final String KEY_CHOOSE_CORRECTLY = "choose_correctly";
    private static final String KEY_REPORT_ID = "report_id";
    private static final String KEY_DAY_TO_START_ON = "day_to_start_on";
    private static final String KEY_AUTO_FILL_LOGIN = "auto_fill_login";
    private static final String KEY_AUTO_SUBMIT_LOGIN = "auto_submit_login";
    private static final String KEY_DESKTOP_MODE = "desktop_mode";
    private static final String KEY_AUTOMATION_RUNNING = "automation_running";
    private static final String KEY_BACKGROUND_KEEP_ALIVE = "background_keep_alive";
    private static final String KEY_OOBE_DONE = "oobe_done";
    private static final String KEY_NOTIFICATION_PERMISSION_REQUESTED = "notification_permission_requested";

    private static final String MODE_VIDEO = "video";
    private static final String DEFAULT_URL = "https://teacher.ewt360.com/";
    private static final String HOMEWORK_DISCOVERY_URL = "https://teacher.ewt360.com/ewtbend/bend/index/index.html#/student/homework";
    private static final String EXTENSION_URI = "resource://android/assets/autoewt/";
    private static final String EXTENSION_ID = "autoewt-geckoview@local";
    private static final int REQUEST_POST_NOTIFICATIONS = 41;
    private static GeckoRuntime sharedRuntime;

    private GeckoRuntime runtime;
    private GeckoSession session;
    private GeckoView geckoView;
    private AutoEwtUiState uiState;
    private EditText urlInput;
    private ProgressBar progressBar;
    private TextView statusText;
    private TextView logText;
    private TextView logSingleLineText;
    private ScrollView logScrollView;
    private LinearLayout browserPage;
    private ScrollView configPage;
    private Button browserTabButton;
    private Button configTabButton;
    private Button startAutomationButton;
    private Button stopAutomationButton;
    private Button logModeButton;
    private SharedPreferences prefs;
    private String lastUrl = DEFAULT_URL;
    private String pendingChildTaskKind = "";
    private String childTaskKind = "";
    private String pendingListUrlTitle = "";
    private boolean listUrlDiscoveryRunning = false;
    private boolean listUrlCandidateSelectionPending = false;
    private WebExtension.Port activePort;
    private WebExtension bridgeExtension;
    private final ArrayDeque<GeckoSession> parentSessions = new ArrayDeque<>();
    private final ArrayList<WebExtension.Port> connectedPorts = new ArrayList<>();
    private final StringBuilder logBuffer = new StringBuilder();
    private long lastAutomationRestartAt = 0L;
    private boolean returnToOobeAfterListUrlDiscovery = false;
    private static final int LOG_MODE_FULL = 0;
    private static final int LOG_MODE_SINGLE = 1;
    private static final int LOG_MODE_HIDDEN = 2;
    private int logMode = LOG_MODE_SINGLE;

    private EditText usernameInput;
    private EditText passwordInput;
    private EditText listUrlInput;
    private RadioGroup modeGroup;
    private RadioButton videoModeButton;
    private RadioButton paperModeButton;
    private EditText dayInput;
    private CheckBox chooseCorrectlyCheck;
    private EditText reportIdInput;
    private CheckBox autoFillLoginCheck;
    private CheckBox autoSubmitLoginCheck;
    private CheckBox desktopModeCheck;
    private CheckBox backgroundKeepAliveCheck;
    private PowerManager.WakeLock automationWakeLock;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        installCrashGuard();
        lastUrl = prefs.getString(KEY_LAST_URL, configuredListUrl());
        if (lastUrl == null || lastUrl.trim().isEmpty()) {
            lastUrl = DEFAULT_URL;
        }

        createLayout();
        loadConfigIntoForm();
        createRuntime();
        createSession();
        installBridge();
        showBrowserPage();
        updateAutomationButtons();
        syncOobeState();
        syncBackgroundKeepAlive();
        load(lastUrl);
    }

    private void installCrashGuard() {
        Thread.UncaughtExceptionHandler previous = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((thread, throwable) -> {
            try {
                prefs.edit().putBoolean(KEY_AUTOMATION_RUNNING, false).apply();
                scheduleSelfRestart();
            } catch (RuntimeException ignored) {
            }
            if (previous != null) {
                previous.uncaughtException(thread, throwable);
            } else {
                System.exit(2);
            }
        });
    }

    private void scheduleSelfRestart() {
        Intent intent = new Intent(this, MainActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pendingIntent = PendingIntent.getActivity(
                this,
                7,
                intent,
                PendingIntent.FLAG_CANCEL_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        AlarmManager alarmManager = (AlarmManager) getSystemService(ALARM_SERVICE);
        if (alarmManager != null) {
            alarmManager.set(
                    AlarmManager.ELAPSED_REALTIME,
                    android.os.SystemClock.elapsedRealtime() + 5000,
                    pendingIntent
            );
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
    }

    @Override
    protected void onResume() {
        super.onResume();
        updateNotificationPermissionState();
        syncBackgroundKeepAlive();
    }

    @Override
    public void onBackPressed() {
        if (prefs != null && prefs.getBoolean(KEY_AUTOMATION_RUNNING, false)) {
            log("任务运行中，已退到后台；需要结束请先点击停止");
            moveTaskToBack(true);
            return;
        }
        super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        boolean running = prefs != null && prefs.getBoolean(KEY_AUTOMATION_RUNNING, false);
        if (!running || isFinishing()) {
            releaseAutomationWakeLock();
            stopBackgroundService();
        }
        while (!parentSessions.isEmpty()) {
            try {
                parentSessions.pop().close();
            } catch (RuntimeException ignored) {
            }
        }
        if (session != null) {
            session.close();
            session = null;
        }
    }

    private void createLayout() {
        uiState = new AutoEwtUiState();
        uiState.setUrl(lastUrl);
        uiState.setLogMode(logMode);
        geckoView = new GeckoView(this);
        AutoEwtComposeUi.install(this, this);
    }

    private void createBrowserPage(LinearLayout parent) {
        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setOrientation(LinearLayout.HORIZONTAL);
        toolbar.setGravity(Gravity.CENTER_VERTICAL);
        toolbar.setPadding(dp(10), dp(0), dp(10), dp(4));
        parent.addView(toolbar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        urlInput = new EditText(this);
        urlInput.setSingleLine(true);
        urlInput.setText(lastUrl);
        urlInput.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        toolbar.addView(urlInput, new LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1
        ));

        Button loadButton = new Button(this);
        loadButton.setText("打开");
        setButtonIcon(loadButton, R.drawable.ic_open);
        loadButton.setOnClickListener(v -> load(urlInput.getText().toString()));
        toolbar.addView(loadButton);

        FlowLayout actionBar = new FlowLayout(this);
        actionBar.setPadding(dp(8), dp(0), dp(8), dp(6));
        actionBar.setItemSpacing(dp(8));
        actionBar.setLineSpacing(dp(6));
        parent.addView(actionBar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        Button openCourseButton = new Button(this);
        openCourseButton.setText("打开课程");
        setButtonIcon(openCourseButton, R.drawable.ic_course);
        openCourseButton.setOnClickListener(v -> openConfiguredCourse());
        addActionButton(actionBar, openCourseButton);

        startAutomationButton = new Button(this);
        startAutomationButton.setText("开始刷课");
        setButtonIcon(startAutomationButton, R.drawable.ic_play);
        startAutomationButton.setOnClickListener(v -> startAutomation());
        addActionButton(actionBar, startAutomationButton);

        stopAutomationButton = new Button(this);
        stopAutomationButton.setText("停止");
        setButtonIcon(stopAutomationButton, R.drawable.ic_stop);
        stopAutomationButton.setOnClickListener(v -> stopAutomation());
        addActionButton(actionBar, stopAutomationButton);

        Button fillLoginButton = new Button(this);
        fillLoginButton.setText("填登录");
        setButtonIcon(fillLoginButton, R.drawable.ic_login);
        fillLoginButton.setOnClickListener(v -> requestFillLogin());
        addActionButton(actionBar, fillLoginButton);

        Button probeButton = new Button(this);
        probeButton.setText("探测");
        setButtonIcon(probeButton, R.drawable.ic_probe);
        probeButton.setOnClickListener(v -> requestProbe());
        addActionButton(actionBar, probeButton);

        Button restartButton = new Button(this);
        restartButton.setText("重启");
        setButtonIcon(restartButton, R.drawable.ic_restart);
        restartButton.setOnClickListener(v -> restartSession());
        addActionButton(actionBar, restartButton);

        logModeButton = new Button(this);
        setButtonIcon(logModeButton, R.drawable.ic_log_full);
        logModeButton.setOnClickListener(v -> cycleLogMode());
        addActionButton(actionBar, logModeButton);

        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(100);
        parent.addView(progressBar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                dp(3)
        ));

        statusText = new TextView(this);
        statusText.setTextColor(Color.rgb(40, 40, 40));
        statusText.setPadding(dp(12), dp(8), dp(12), dp(8));
        statusText.setText("就绪");
        parent.addView(statusText);

        geckoView = new GeckoView(this);
        parent.addView(geckoView, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0,
                1
        ));

        logSingleLineText = new TextView(this);
        logSingleLineText.setSingleLine(true);
        logSingleLineText.setTextColor(Color.rgb(32, 32, 32));
        logSingleLineText.setTextSize(12);
        logSingleLineText.setPadding(dp(12), dp(6), dp(12), dp(6));
        parent.addView(logSingleLineText, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        logScrollView = new ScrollView(this);
        logScrollView.setFillViewport(false);
        logText = new TextView(this);
        logText.setTextColor(Color.rgb(32, 32, 32));
        logText.setTextSize(12);
        logText.setPadding(dp(12), dp(8), dp(12), dp(8));
        logScrollView.addView(logText);
        parent.addView(logScrollView, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                dp(150)
        ));
        applyLogModeLayout();
    }

    private void addActionButton(FlowLayout parent, Button button) {
        button.setMinWidth(dp(104));
        button.setAllCaps(false);
        button.setGravity(Gravity.CENTER);
        ViewGroup.MarginLayoutParams params = new ViewGroup.MarginLayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        int margin = dp(2);
        params.setMargins(margin, margin, margin, margin);
        parent.addView(button, params);
    }

    private void setButtonIcon(Button button, int drawableResId) {
        Drawable icon = getResources().getDrawable(drawableResId, getTheme()).mutate();
        icon.setTint(Color.rgb(20, 90, 160));
        button.setCompoundDrawablesWithIntrinsicBounds(icon, null, null, null);
        button.setCompoundDrawablePadding(dp(6));
    }

    private void cycleLogMode() {
        logMode = (logMode + 1) % 3;
        applyLogModeLayout();
    }

    private void applyLogModeLayout() {
        if (uiState != null) {
            uiState.setLogMode(logMode);
        }
        if (logModeButton != null) {
            if (logMode == LOG_MODE_FULL) {
                logModeButton.setText("日志:完整");
                setButtonIcon(logModeButton, R.drawable.ic_log_full);
            } else if (logMode == LOG_MODE_SINGLE) {
                logModeButton.setText("日志:单行");
                setButtonIcon(logModeButton, R.drawable.ic_log_single);
            } else {
                logModeButton.setText("日志:隐藏");
                setButtonIcon(logModeButton, R.drawable.ic_log_hidden);
            }
        }
        if (logSingleLineText != null) {
            logSingleLineText.setVisibility(logMode == LOG_MODE_SINGLE ? View.VISIBLE : View.GONE);
        }
        if (logScrollView != null) {
            logScrollView.setVisibility(logMode == LOG_MODE_FULL ? View.VISIBLE : View.GONE);
        }
    }

    private ScrollView createConfigPage() {
        ScrollView scrollView = new ScrollView(this);
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(dp(16), dp(8), dp(16), dp(16));
        scrollView.addView(form);

        addSectionTitle(form, "账号与课程");
        usernameInput = addEditRow(form, "账号", "用于登录课程列表页面", false, InputType.TYPE_CLASS_TEXT);
        passwordInput = addEditRow(
                form,
                "密码",
                "仅保存在本机应用数据中",
                true,
                InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD
        );
        listUrlInput = addEditRow(form, "课程列表 URL", "README 中的 list_url，保存后可直接打开", false, InputType.TYPE_TEXT_VARIATION_URI);

        dayInput = addEditRow(form, "从第几天开始", "默认 1", false, InputType.TYPE_CLASS_NUMBER);
        chooseCorrectlyCheck = new CheckBox(this);
        reportIdInput = new EditText(this);

        addSectionTitle(form, "高级设置");
        addWarningText(form, "高级设置会改变自动化入口、登录行为和浏览器 UA/viewport。改错可能导致无法登录、日期/课程识别异常或触发浏览器重启；除非排查兼容问题，建议保持默认。");

        addSectionTitle(form, "运行模式");
        modeGroup = new RadioGroup(this);
        modeGroup.setOrientation(RadioGroup.HORIZONTAL);
        videoModeButton = new RadioButton(this);
        videoModeButton.setText("刷课");
        paperModeButton = new RadioButton(this);
        paperModeButton.setVisibility(View.GONE);
        modeGroup.addView(videoModeButton);
        modeGroup.addView(paperModeButton);
        form.addView(modeGroup);

        addSectionTitle(form, "登录与浏览器兼容");
        autoFillLoginCheck = addCheckRow(form, "进入登录页后自动填入账号密码");
        autoSubmitLoginCheck = addCheckRow(form, "填入后自动点击登录按钮");
        desktopModeCheck = addCheckRow(form, "使用桌面浏览器模式打开网页");

        addSectionTitle(form, "后台运行");
        backgroundKeepAliveCheck = addCheckRow(form, "运行时显示常驻通知并保持 CPU 唤醒");

        LinearLayout buttonRow = new LinearLayout(this);
        buttonRow.setOrientation(LinearLayout.HORIZONTAL);
        buttonRow.setGravity(Gravity.CENTER_VERTICAL);
        buttonRow.setPadding(0, dp(12), 0, dp(4));
        form.addView(buttonRow);

        Button saveButton = new Button(this);
        saveButton.setText("保存配置");
        saveButton.setOnClickListener(v -> saveConfigFromForm(false));
        buttonRow.addView(saveButton, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        Button saveOpenButton = new Button(this);
        saveOpenButton.setText("保存并打开");
        saveOpenButton.setOnClickListener(v -> {
            saveConfigFromForm(false);
            showBrowserPage();
            openConfiguredCourse();
        });
        buttonRow.addView(saveOpenButton, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        Button discoverButton = new Button(this);
        discoverButton.setText("自动获取课程列表 URL");
        discoverButton.setOnClickListener(v -> discoverListUrlFromUi());
        LinearLayout.LayoutParams discoverParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        discoverParams.setMargins(0, dp(8), 0, 0);
        form.addView(discoverButton, discoverParams);

        TextView hint = new TextView(this);
        hint.setText("提示：如果登录页有验证码，应用只会填入账号密码，不会绕过验证码。若网页显示异常，可切换桌面浏览器模式后重启内核。");
        hint.setTextColor(Color.rgb(96, 96, 96));
        hint.setTextSize(13);
        hint.setPadding(0, dp(8), 0, 0);
        form.addView(hint);

        return scrollView;
    }

    private void addSectionTitle(LinearLayout parent, String title) {
        TextView view = new TextView(this);
        view.setText(title);
        view.setTextColor(Color.rgb(20, 20, 20));
        view.setTextSize(18);
        view.setPadding(0, dp(16), 0, dp(6));
        parent.addView(view);
    }

    private void addWarningText(LinearLayout parent, String text) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextColor(Color.rgb(126, 43, 32));
        view.setTextSize(13);
        view.setPadding(dp(12), dp(10), dp(12), dp(10));
        view.setBackgroundColor(Color.rgb(255, 237, 232));
        parent.addView(view, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));
    }

    private EditText addEditRow(
            LinearLayout parent,
            String label,
            String hint,
            boolean password,
            int inputType
    ) {
        TextView title = new TextView(this);
        title.setText(label);
        title.setTextColor(Color.rgb(54, 54, 54));
        title.setTextSize(14);
        title.setPadding(0, dp(8), 0, dp(2));
        parent.addView(title);

        EditText editText = new EditText(this);
        editText.setSingleLine(true);
        editText.setHint(hint);
        editText.setInputType(inputType);
        if (password) {
            editText.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        }
        parent.addView(editText, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        return editText;
    }

    private CheckBox addCheckRow(LinearLayout parent, String text) {
        CheckBox checkBox = new CheckBox(this);
        checkBox.setText(text);
        checkBox.setTextColor(Color.rgb(54, 54, 54));
        checkBox.setPadding(0, dp(6), 0, dp(2));
        parent.addView(checkBox);
        return checkBox;
    }

    @Override
    public AutoEwtUiState getState() {
        return uiState;
    }

    @Override
    public GeckoView geckoView() {
        return geckoView;
    }

    @Override
    public void showBrowserPage() {
        if (uiState != null) {
            uiState.setPage(AutoEwtUiState.PAGE_BROWSER);
        }
        if (browserPage != null) {
            browserPage.setVisibility(View.VISIBLE);
        }
        if (configPage != null) {
            configPage.setVisibility(View.GONE);
        }
        if (browserTabButton != null) {
            browserTabButton.setEnabled(false);
        }
        if (configTabButton != null) {
            configTabButton.setEnabled(true);
        }
    }

    @Override
    public void showConfigPage() {
        loadConfigIntoForm();
        if (uiState != null) {
            uiState.setPage(AutoEwtUiState.PAGE_CONFIG);
        }
        if (browserPage != null) {
            browserPage.setVisibility(View.GONE);
        }
        if (configPage != null) {
            configPage.setVisibility(View.VISIBLE);
        }
        if (browserTabButton != null) {
            browserTabButton.setEnabled(true);
        }
        if (configTabButton != null) {
            configTabButton.setEnabled(false);
        }
    }

    @Override
    public void setBrowserVisibleFromUi(boolean visible) {
        if (uiState != null) {
            uiState.setBrowserVisible(visible);
        }
        refreshGeckoViewAfterVisibilityChange();
    }

    private void refreshGeckoViewAfterVisibilityChange() {
        if (geckoView == null) {
            return;
        }
        geckoView.postDelayed(this::refreshGeckoViewSurface, 80);
        geckoView.postDelayed(this::refreshGeckoViewSurface, 260);
    }

    private void refreshGeckoViewSurface() {
        if (geckoView == null) {
            return;
        }
        geckoView.requestLayout();
        geckoView.invalidate();
    }

    @Override
    public void cycleLogModeFromUi() {
        cycleLogMode();
    }

    @Override
    public void openUrlFromUi(String url) {
        showBrowserPage();
        load(url);
    }

    @Override
    public void openConfiguredCourseFromUi() {
        saveConfigFromForm(true);
        showBrowserPage();
        openConfiguredCourse();
    }

    @Override
    public void discoverListUrlFromUi() {
        saveConfigFromForm(true);
        if (!validateCredentialsForDiscovery()) {
            return;
        }
        showBrowserForListUrlDiscovery();
        listUrlDiscoveryRunning = true;
        listUrlCandidateSelectionPending = false;
        pendingListUrlTitle = "";
        setListUrlManualEntryVisible(false);
        setListUrlDiscoveryUi(true, "正在准备获取任务");
        if (!returnToOobeAfterListUrlDiscovery) {
            showBrowserPage();
            setBrowserVisibleFromUi(true);
        }
        if (geckoView != null) {
            geckoView.postDelayed(this::startListUrlDiscovery, 150);
        } else {
            startListUrlDiscovery();
        }
    }

    @Override
    public void selectListUrlCandidateFromUi(String candidateId, String title) {
        if (uiState != null) {
            uiState.clearListUrlCandidates();
        }
        sendListUrlCandidateSelection(candidateId, title);
    }

    @Override
    public void cancelListUrlDiscoveryFromUi() {
        cancelListUrlDiscovery("用户取消选择");
    }

    @Override
    public void startAutomationFromUi() {
        saveConfigFromForm(true);
        startAutomation();
    }

    @Override
    public void stopAutomationFromUi() {
        stopAutomation();
    }

    @Override
    public void requestFillLoginFromUi() {
        saveConfigFromForm(true);
        requestFillLogin();
    }

    @Override
    public void requestProbeFromUi() {
        requestProbe();
    }

    @Override
    public void restartSessionFromUi() {
        restartSession();
    }

    @Override
    public void saveConfigFromUi() {
        saveConfigFromForm(false);
    }

    @Override
    public void saveAndOpenFromUi() {
        saveConfigFromForm(false);
        showBrowserPage();
        openConfiguredCourse();
    }

    @Override
    public void dismissOobeFromUi() {
        returnToOobeAfterListUrlDiscovery = false;
        prefs.edit().putBoolean(KEY_OOBE_DONE, true).apply();
        if (uiState != null) {
            uiState.setOobeVisible(false);
        }
    }

    @Override
    public void openConfigFromOobeFromUi() {
        dismissOobeFromUi();
        showConfigPage();
    }

    @Override
    public void resetAppToOobeFromUi() {
        sendAutomationCommand("stop");
        sendStopListUrlDiscoveryCommand();
        closeAllChildSessions("reset");
        releaseAutomationWakeLock();
        stopBackgroundService();

        returnToOobeAfterListUrlDiscovery = false;
        listUrlDiscoveryRunning = false;
        listUrlCandidateSelectionPending = false;
        pendingListUrlTitle = "";
        pendingChildTaskKind = "";
        childTaskKind = "";
        lastAutomationRestartAt = 0L;
        logMode = LOG_MODE_SINGLE;
        lastUrl = DEFAULT_URL;
        logBuffer.setLength(0);

        prefs.edit().clear().putBoolean(KEY_OOBE_DONE, false).apply();
        loadConfigIntoForm();
        if (uiState != null) {
            uiState.setPage(AutoEwtUiState.PAGE_BROWSER);
            uiState.setBrowserVisible(true);
            uiState.setOobeStep(0);
            uiState.setOobeVisible(true);
            uiState.setLogMode(LOG_MODE_SINGLE);
            uiState.setFullLog("");
            uiState.setSingleLogLine("");
            uiState.clearAutomationProgress();
            uiState.updateListUrlDiscovery(false, "");
            uiState.setListUrlManualEntryVisible(false);
            uiState.setStatus("已回到初始化向导");
        }
        setUrlText(DEFAULT_URL);
        updateAutomationButtons();
        syncBackgroundKeepAlive();
        load(DEFAULT_URL);
        log("已重新初始化软件并回到 OOBE");
    }

    @Override
    public void requestNotificationPermissionFromUi() {
        requestPostNotificationsIfNeeded(true);
        updateNotificationPermissionState();
    }

    private void showBrowserForListUrlDiscovery() {
        returnToOobeAfterListUrlDiscovery = uiState != null && uiState.getOobeVisible();
        if (returnToOobeAfterListUrlDiscovery && uiState != null) {
            uiState.setOobeStep(2);
        }
    }

    private void restoreOobeAfterListUrlDiscoveryIfNeeded(boolean success) {
        if (!returnToOobeAfterListUrlDiscovery || uiState == null || prefs.getBoolean(KEY_OOBE_DONE, false)) {
            returnToOobeAfterListUrlDiscovery = false;
            return;
        }
        returnToOobeAfterListUrlDiscovery = false;
        uiState.setOobeStep(success ? 3 : 2);
        uiState.setOobeVisible(true);
        uiState.setPage(AutoEwtUiState.PAGE_CONFIG);
    }

    private boolean validateCredentialsForDiscovery() {
        String username = prefs.getString(KEY_USERNAME, "");
        String password = prefs.getString(KEY_PASSWORD, "");
        if (!username.trim().isEmpty() && !password.isEmpty()) {
            return true;
        }
        listUrlDiscoveryRunning = false;
        pendingListUrlTitle = "";
        setListUrlManualEntryVisible(false);
        setListUrlDiscoveryUi(false, "请先填写账号和密码，再自动获取任务。");
        if (uiState != null) {
            uiState.setOobeStep(2);
            uiState.setPage(AutoEwtUiState.PAGE_CONFIG);
        }
        showConfigPage();
        log("请先填写账号和密码，再自动获取课程列表 URL");
        return false;
    }

    private void createRuntime() {
        if (sharedRuntime != null) {
            runtime = sharedRuntime;
            return;
        }
        GeckoRuntimeSettings settings = new GeckoRuntimeSettings.Builder()
                .javaScriptEnabled(true)
                .remoteDebuggingEnabled(BuildConfig.DEBUG)
                .consoleOutput(BuildConfig.DEBUG)
                .build();
        sharedRuntime = GeckoRuntime.create(getApplicationContext(), settings);
        runtime = sharedRuntime;
    }

    private void createSession() {
        while (!parentSessions.isEmpty()) {
            try {
                parentSessions.pop().close();
            } catch (RuntimeException ignored) {
            }
        }
        session = buildSession();
        session.open(runtime);
        geckoView.setSession(session);
    }

    private GeckoSession buildSession() {
        GeckoSessionSettings.Builder settingsBuilder = new GeckoSessionSettings.Builder()
                .allowJavascript(true);
        if (prefs.getBoolean(KEY_DESKTOP_MODE, true)) {
            settingsBuilder
                    .userAgentMode(GeckoSessionSettings.USER_AGENT_MODE_DESKTOP)
                    .viewportMode(GeckoSessionSettings.VIEWPORT_MODE_DESKTOP);
        } else {
            settingsBuilder
                    .userAgentMode(GeckoSessionSettings.USER_AGENT_MODE_MOBILE)
                    .viewportMode(GeckoSessionSettings.VIEWPORT_MODE_MOBILE);
        }

        GeckoSession targetSession = new GeckoSession(settingsBuilder.build());
        targetSession.setProgressDelegate(new GeckoSession.ProgressDelegate() {
            @Override
            public void onPageStart(GeckoSession geckoSession, String url) {
                status("加载中：" + url);
                setLoadProgress(0);
            }

            @Override
            public void onPageStop(GeckoSession geckoSession, boolean success) {
                status(success ? "页面加载完成" : "页面加载失败");
                setLoadProgress(success ? 100 : 0);
                sendConfigToPage(false);
                sendListUrlDiscoveryCommand();
            }

            @Override
            public void onProgressChange(GeckoSession geckoSession, int progress) {
                setLoadProgress(progress);
            }
        });

        targetSession.setContentDelegate(new GeckoSession.ContentDelegate() {
            @Override
            public void onTitleChange(GeckoSession geckoSession, String title) {
                status("标题：" + title);
            }

            @Override
            public void onCrash(GeckoSession geckoSession) {
                log("内容进程崩溃，正在恢复 GeckoSession");
                restartSession();
            }

            @Override
            public void onKill(GeckoSession geckoSession) {
                log("内容进程被系统终止，正在恢复 GeckoSession");
                restartSession();
            }
        });

        targetSession.setNavigationDelegate(new GeckoSession.NavigationDelegate() {
            @Override
            public GeckoResult<GeckoSession> onNewSession(GeckoSession geckoSession, String uri) {
                captureDiscoveredListUrlFromNavigation(uri);
                GeckoSession childSession = buildSession();
                parentSessions.push(MainActivity.this.session);
                childTaskKind = pendingChildTaskKind;
                pendingChildTaskKind = "";
                notifyChildSessionOpened(uri);
                MainActivity.this.session = childSession;
                activePort = null;
                attachBridgeToSession(childSession);
                geckoView.setSession(childSession);
                log("已接管网页新窗口：" + uri);
                return GeckoResult.fromValue(childSession);
            }

            @Override
            public GeckoResult<AllowOrDeny> onLoadRequest(GeckoSession geckoSession, GeckoSession.NavigationDelegate.LoadRequest request) {
                if (request.target == GeckoSession.NavigationDelegate.TARGET_WINDOW_NEW) {
                    log("检测到新窗口请求：" + request.uri);
                    captureDiscoveredListUrlFromNavigation(request.uri);
                }
                return GeckoResult.fromValue(AllowOrDeny.ALLOW);
            }

            @Override
            public void onLocationChange(
                    GeckoSession geckoSession,
                    String url,
                    java.util.List<GeckoSession.PermissionDelegate.ContentPermission> perms,
                    Boolean hasUserGesture
            ) {
                if (geckoSession == MainActivity.this.session && url != null && !url.isEmpty()) {
                    captureDiscoveredListUrlFromNavigation(url);
                    lastUrl = url;
                    if (shouldPersistUrl(url)) {
                        prefs.edit().putString(KEY_LAST_URL, url).apply();
                    }
                    setUrlText(url);
                }
            }
        });

        attachBridgeToSession(targetSession);
        return targetSession;
    }

    private void installBridge() {
        runtime.getWebExtensionController()
                .ensureBuiltIn(EXTENSION_URI, EXTENSION_ID)
                .accept(extension -> {
                    bridgeExtension = extension;
                    attachBridgeToSession(session);
                    log("WebExtension bridge ready: " + EXTENSION_ID);
                }, throwable -> log("WebExtension bridge failed: " + throwable));
    }

    private void attachBridgeToSession(GeckoSession targetSession) {
        if (bridgeExtension == null || targetSession == null) {
            return;
        }
        targetSession.getWebExtensionController().setMessageDelegate(
                bridgeExtension,
                new AutoEwtMessageDelegate(),
                "autoewt"
        );
    }

    private void load(String rawUrl) {
        String url = normalizeUrl(rawUrl);
        lastUrl = url;
        setUrlText(url);
        if (shouldPersistUrl(url)) {
            prefs.edit().putString(KEY_LAST_URL, url).apply();
        }
        log("打开：" + url);
        session.loadUri(url);
    }

    private boolean shouldPersistUrl(String url) {
        return url != null
                && !url.contains("/homework/play-videos")
                && !url.contains("#/homework/play-videos")
                && !url.contains("/play-videos");
    }

    private void openConfiguredCourse() {
        String url = configuredListUrl();
        if (url.isEmpty()) {
            log("还没有配置课程列表 URL，请先到配置页填写");
            showConfigPage();
            return;
        }
        load(url);
    }

    private void requestProbe() {
        if (activePort == null) {
            log("WebExtension port 尚未连接，页面加载完成后再试");
            return;
        }
        JSONObject message = new JSONObject();
        try {
            message.put("type", "probe");
            message.put("at", System.currentTimeMillis());
        } catch (JSONException ignored) {
        }
        activePort.postMessage(message);
        log("已请求页面状态探测");
    }

    private void requestFillLogin() {
        if (activePort == null) {
            log("WebExtension port 尚未连接，页面加载完成后再试");
            return;
        }
        sendConfigToPage(false);
        JSONObject message = new JSONObject();
        try {
            message.put("type", "fillLogin");
            message.put("submit", prefs.getBoolean(KEY_AUTO_SUBMIT_LOGIN, true));
            message.put("at", System.currentTimeMillis());
        } catch (JSONException ignored) {
        }
        activePort.postMessage(message);
        log("已请求填入登录表单");
    }

    private void startListUrlDiscovery() {
        String username = prefs.getString(KEY_USERNAME, "");
        String password = prefs.getString(KEY_PASSWORD, "");
        if (username.trim().isEmpty() || password.isEmpty()) {
            pendingListUrlTitle = "";
            setListUrlManualEntryVisible(false);
            setListUrlDiscoveryUi(false, "请先填写账号和密码，再自动获取任务。");
            if (uiState != null) {
                uiState.setOobeStep(2);
            }
            log("请先填写账号和密码，再自动获取课程列表 URL");
            restoreOobeAfterListUrlDiscoveryIfNeeded(false);
            showConfigPage();
            return;
        }
        prefs.edit()
                .putBoolean(KEY_AUTOMATION_RUNNING, false)
                .putBoolean(KEY_AUTO_FILL_LOGIN, true)
                .putBoolean(KEY_AUTO_SUBMIT_LOGIN, true)
                .apply();
        listUrlDiscoveryRunning = true;
        listUrlCandidateSelectionPending = false;
        setListUrlManualEntryVisible(false);
        setListUrlDiscoveryUi(true, "正在打开任务页");
        pendingChildTaskKind = "";
        childTaskKind = "";
        updateAutomationButtons();
        closeAllChildSessions("discoverListUrl");
        boolean keepInOobe = returnToOobeAfterListUrlDiscovery && uiState != null && uiState.getOobeVisible();
        if (!keepInOobe) {
            showBrowserPage();
            setBrowserVisibleFromUi(true);
        } else {
            uiState.setOobeStep(2);
        }
        log("开始自动获取课程列表 URL：将隐藏已完成任务，并扫描进行中、未开始、已截止任务");
        load(HOMEWORK_DISCOVERY_URL);
        if (geckoView != null) {
            geckoView.postDelayed(this::sendListUrlDiscoveryCommand, 500);
        } else {
            sendListUrlDiscoveryCommand();
        }
    }

    private void sendListUrlDiscoveryCommand() {
        if (!listUrlDiscoveryRunning) {
            return;
        }
        JSONObject message = new JSONObject();
        try {
            message.put("type", "discoverListUrl");
            message.put("targetUrl", HOMEWORK_DISCOVERY_URL);
            message.put("config", buildConfigJson());
            message.put("at", System.currentTimeMillis());
        } catch (JSONException ignored) {
        }
        postToConnectedPorts(message);
    }

    private void sendStopListUrlDiscoveryCommand() {
        JSONObject message = new JSONObject();
        try {
            message.put("type", "stopListUrlDiscovery");
            message.put("at", System.currentTimeMillis());
        } catch (JSONException ignored) {
        }
        postToConnectedPorts(message);
    }

    private void setListUrlDiscoveryUi(boolean running, String message) {
        if (uiState != null) {
            uiState.updateListUrlDiscovery(running, message == null ? "" : message);
        }
    }

    private void setListUrlManualEntryVisible(boolean visible) {
        if (uiState != null) {
            uiState.setListUrlManualEntryVisible(visible);
        }
    }

    private void sendListUrlCandidateSelection(String candidateId, String title) {
        pendingListUrlTitle = title == null ? "" : title.trim();
        listUrlCandidateSelectionPending = true;
        if (uiState != null) {
            uiState.clearListUrlCandidates();
        }
        JSONObject message = new JSONObject();
        try {
            message.put("type", "selectListUrlCandidate");
            message.put("candidateId", candidateId);
            message.put("at", System.currentTimeMillis());
        } catch (JSONException ignored) {
        }
        postToConnectedPorts(message);
        setListUrlDiscoveryUi(true, "正在打开所选任务：" + title);
        log("URL 获取：已选择任务：" + title);
    }

    private void cancelListUrlDiscovery(String reason) {
        listUrlDiscoveryRunning = false;
        listUrlCandidateSelectionPending = false;
        sendStopListUrlDiscoveryCommand();
        setListUrlDiscoveryUi(false, "");
        log("URL 获取已取消：" + reason);
        restoreOobeAfterListUrlDiscoveryIfNeeded(false);
    }

    private void showListUrlCandidateDialog(JSONArray candidates) {
        if (listUrlCandidateSelectionPending) {
            setListUrlDiscoveryUi(true, "正在打开所选任务：" + pendingListUrlTitle);
            log("URL 获取：已选择任务，忽略重复候选列表");
            return;
        }
        if (candidates == null || candidates.length() == 0) {
            listUrlDiscoveryRunning = false;
            listUrlCandidateSelectionPending = false;
            setListUrlDiscoveryUi(false, "");
            setListUrlManualEntryVisible(true);
            log("URL 获取失败：没有找到可选任务");
            restoreOobeAfterListUrlDiscoveryIfNeeded(false);
            return;
        }

        ArrayList<AutoEwtListUrlCandidate> candidateItems = new ArrayList<>();
        for (int i = 0; i < candidates.length(); i++) {
            JSONObject candidate = candidates.optJSONObject(i);
            if (candidate == null) {
                continue;
            }
            String id = candidate.optString("id", "");
            if (id.isEmpty()) {
                continue;
            }
            String title = trimForDialog(candidate.optString("title", "未命名任务"), 80);
            String status = trimForDialog(candidate.optString("status", "未完成"), 32);
            String filter = trimForDialog(candidate.optString("filter", ""), 32);
            String startTime = trimForDialog(candidate.optString("startTime", ""), 40);
            String deadline = trimForDialog(candidate.optString("deadline", ""), 40);
            String teacher = trimForDialog(candidate.optString("teacher", ""), 32);

            candidateItems.add(new AutoEwtListUrlCandidate(
                    id,
                    title,
                    status,
                    filter,
                    startTime,
                    deadline,
                    teacher
            ));
        }

        if (candidateItems.isEmpty()) {
            listUrlDiscoveryRunning = false;
            listUrlCandidateSelectionPending = false;
            setListUrlDiscoveryUi(false, "");
            setListUrlManualEntryVisible(true);
            log("URL 获取失败：候选任务数据为空");
            restoreOobeAfterListUrlDiscoveryIfNeeded(false);
            return;
        }

        if (uiState != null) {
            uiState.showListUrlCandidates(candidateItems);
        }
        setListUrlDiscoveryUi(true, "请选择要保存的任务");
    }

    private String trimForDialog(String value, int maxChars) {
        String text = value == null ? "" : value.trim().replaceAll("\\s+", " ");
        if (text.length() <= maxChars) {
            return text;
        }
        return text.substring(0, Math.max(0, maxChars - 1)) + "…";
    }

    private String cleanLoginFailureMessage(String rawMessage) {
        String message = rawMessage == null ? "" : rawMessage.trim().replaceAll("\\s+", " ");
        if (message.contains("登录失败")
                || message.contains("账号")
                || message.contains("账户")
                || message.contains("用户")
                || message.contains("密码")) {
            return "请检查账号密码后重试";
        }
        if (message.contains("验证码") || message.contains("短信")) {
            return "验证码错误，请重新验证后重试";
        }
        return message.isEmpty() ? "请检查账号密码后重试" : message;
    }

    private void startAutomation() {
        String listUrl = configuredListUrl();
        String username = prefs.getString(KEY_USERNAME, "");
        String password = prefs.getString(KEY_PASSWORD, "");
        if (listUrl.isEmpty() || username.trim().isEmpty() || password.isEmpty()) {
            log("缺少账号、密码或课程列表 URL，请先到配置页填写");
            showConfigPage();
            return;
        }
        prefs.edit()
                .putBoolean(KEY_AUTOMATION_RUNNING, true)
                .putBoolean(KEY_AUTO_FILL_LOGIN, true)
                .putBoolean(KEY_AUTO_SUBMIT_LOGIN, true)
                .apply();
        pendingChildTaskKind = "";
        childTaskKind = "";
        listUrlDiscoveryRunning = false;
        if (uiState != null) {
            uiState.clearAutomationProgress();
            uiState.setStatus("自动刷课已启动");
        }
        updateAutomationButtons();
        syncBackgroundKeepAlive();
        log("自动刷课已启动");
        closeAllChildSessions("start");
        showBrowserPage();
        setBrowserVisibleFromUi(true);
        load(listUrl);
        if (geckoView != null) {
            geckoView.postDelayed(() -> {
                if (prefs.getBoolean(KEY_AUTOMATION_RUNNING, false)) {
                    sendAutomationCommand("start");
                }
            }, 3500);
        }
    }

    private void stopAutomation() {
        prefs.edit().putBoolean(KEY_AUTOMATION_RUNNING, false).apply();
        pendingChildTaskKind = "";
        childTaskKind = "";
        listUrlDiscoveryRunning = false;
        if (uiState != null) {
            uiState.clearAutomationProgress();
            uiState.setStatus("自动刷课已停止");
        }
        updateAutomationButtons();
        sendAutomationCommand("stop");
        closeAllChildSessions("stop");
        syncBackgroundKeepAlive();
        log("自动刷课已停止");
    }

    private void sendAutomationCommand(String action) {
        JSONObject message = new JSONObject();
        try {
            message.put("type", "automation");
            message.put("action", action);
            message.put("config", buildConfigJson());
            message.put("at", System.currentTimeMillis());
        } catch (JSONException ignored) {
        }
        postToConnectedPorts(message);
    }

    private void updateAutomationButtons() {
        boolean running = prefs.getBoolean(KEY_AUTOMATION_RUNNING, false);
        if (uiState != null) {
            uiState.setAutomationRunning(running);
        }
        syncBackgroundStateIntoUi();
        if (startAutomationButton == null || stopAutomationButton == null) {
            return;
        }
        startAutomationButton.setEnabled(!running);
        stopAutomationButton.setEnabled(running);
    }

    private void syncOobeState() {
        if (uiState == null) {
            return;
        }
        boolean running = prefs.getBoolean(KEY_AUTOMATION_RUNNING, false);
        uiState.setOobeVisible(!running && !prefs.getBoolean(KEY_OOBE_DONE, false));
        syncBackgroundStateIntoUi();
    }

    private void syncBackgroundStateIntoUi() {
        if (uiState == null || prefs == null) {
            return;
        }
        uiState.setBackgroundKeepAlive(prefs.getBoolean(KEY_BACKGROUND_KEEP_ALIVE, true));
        uiState.setNotificationPermissionGranted(hasNotificationPermission());
    }

    private void updateNotificationPermissionState() {
        if (uiState != null) {
            uiState.setNotificationPermissionGranted(hasNotificationPermission());
        }
    }

    private void syncBackgroundKeepAlive() {
        boolean running = prefs != null && prefs.getBoolean(KEY_AUTOMATION_RUNNING, false);
        boolean keepAlive = prefs != null && prefs.getBoolean(KEY_BACKGROUND_KEEP_ALIVE, true);
        syncBackgroundStateIntoUi();
        if (running && keepAlive) {
            requestPostNotificationsIfNeeded(false);
            startBackgroundService();
            acquireAutomationWakeLock();
        } else {
            stopBackgroundService();
            releaseAutomationWakeLock();
        }
    }

    private void startBackgroundService() {
        Intent intent = new Intent(this, AutoEwtForegroundService.class);
        intent.setAction(AutoEwtForegroundService.ACTION_START);
        intent.putExtra(AutoEwtForegroundService.EXTRA_STATUS, backgroundNotificationStatus());
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(intent);
            } else {
                startService(intent);
            }
        } catch (RuntimeException error) {
            log("后台保活通知启动失败，继续使用 WakeLock 保底：" + error.getClass().getSimpleName());
        }
    }

    private void stopBackgroundService() {
        try {
            stopService(new Intent(this, AutoEwtForegroundService.class));
        } catch (RuntimeException ignored) {
        }
    }

    private String backgroundNotificationStatus() {
        return "刷课运行中，点击返回浏览器界面";
    }

    private void acquireAutomationWakeLock() {
        if (automationWakeLock != null && automationWakeLock.isHeld()) {
            return;
        }
        PowerManager powerManager = (PowerManager) getSystemService(POWER_SERVICE);
        if (powerManager == null) {
            return;
        }
        automationWakeLock = powerManager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                "AutoEwt:AutomationKeepAlive"
        );
        automationWakeLock.setReferenceCounted(false);
        automationWakeLock.acquire();
    }

    private void releaseAutomationWakeLock() {
        if (automationWakeLock == null) {
            return;
        }
        try {
            if (automationWakeLock.isHeld()) {
                automationWakeLock.release();
            }
        } catch (RuntimeException ignored) {
        }
        automationWakeLock = null;
    }

    private void requestPostNotificationsIfNeeded(boolean force) {
        if (Build.VERSION.SDK_INT < 33 || hasNotificationPermission()) {
            return;
        }
        if (!force && prefs.getBoolean(KEY_NOTIFICATION_PERMISSION_REQUESTED, false)) {
            return;
        }
        prefs.edit().putBoolean(KEY_NOTIFICATION_PERMISSION_REQUESTED, true).apply();
        requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQUEST_POST_NOTIFICATIONS);
    }

    private boolean hasNotificationPermission() {
        return Build.VERSION.SDK_INT < 33
                || checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED;
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQUEST_POST_NOTIFICATIONS) {
            updateNotificationPermissionState();
            if (hasNotificationPermission()) {
                log("通知权限已允许，后台运行通知可正常显示");
            } else {
                log("通知权限未开启，后台运行保活能力会降低");
            }
            syncBackgroundKeepAlive();
        }
    }

    private void sendConfigToPage(boolean verbose) {
        JSONObject message = new JSONObject();
        try {
            message.put("type", "config");
            message.put("config", buildConfigJson());
            message.put("at", System.currentTimeMillis());
        } catch (JSONException ignored) {
        }
        postToConnectedPorts(message);
        if (verbose) {
            log("已同步配置到页面");
        }
        if (prefs.getBoolean(KEY_AUTOMATION_RUNNING, false)) {
            sendAutomationCommand("start");
        }
    }

    private void postToConnectedPorts(JSONObject message) {
        if (connectedPorts.isEmpty()) {
            return;
        }
        ArrayList<WebExtension.Port> snapshot = new ArrayList<>(connectedPorts);
        for (WebExtension.Port port : snapshot) {
            try {
                port.postMessage(message);
            } catch (RuntimeException ignored) {
                connectedPorts.remove(port);
                if (activePort == port) {
                    activePort = null;
                }
            }
        }
    }

    private void restartSession() {
        log("重启 GeckoSession");
        while (!parentSessions.isEmpty()) {
            try {
                parentSessions.pop().close();
            } catch (RuntimeException ignored) {
            }
        }
        if (session != null) {
            try {
                session.close();
            } catch (RuntimeException ignored) {
            }
        }
        activePort = null;
        connectedPorts.clear();
        pendingChildTaskKind = "";
        childTaskKind = "";
        createSession();
        installBridge();
        load(lastUrl);
    }

    private void restartBrowserFromAutomation(String reason, String sourceUrl) {
        long now = SystemClock.uptimeMillis();
        if (now - lastAutomationRestartAt < 45000L) {
            log("自动化请求重启过于频繁，已忽略：" + reason);
            return;
        }
        lastAutomationRestartAt = now;
        String listUrl = configuredListUrl();
        boolean running = prefs.getBoolean(KEY_AUTOMATION_RUNNING, false);
        if (running && !listUrl.isEmpty()) {
            lastUrl = listUrl;
        } else if (sourceUrl != null && !sourceUrl.trim().isEmpty() && shouldPersistUrl(sourceUrl)) {
            lastUrl = sourceUrl;
        }
        log("检测到自动化卡死，重启浏览器：" + reason);
        restartSession();
    }

    private void saveDiscoveredListUrl(String rawUrl, String title) {
        String url = normalizeOptionalUrl(rawUrl);
        String taskTitle = title == null || title.trim().isEmpty() ? pendingListUrlTitle : title.trim();
        if (!isDiscoveredListUrl(url)) {
            listUrlDiscoveryRunning = false;
            listUrlCandidateSelectionPending = false;
            pendingListUrlTitle = "";
            setListUrlDiscoveryUi(false, "");
            setListUrlManualEntryVisible(true);
            sendStopListUrlDiscoveryCommand();
            log("URL 获取结果无效：" + rawUrl);
            restoreOobeAfterListUrlDiscoveryIfNeeded(false);
            return;
        }
        listUrlDiscoveryRunning = false;
        listUrlCandidateSelectionPending = false;
        setListUrlDiscoveryUi(false, "");
        setListUrlManualEntryVisible(false);
        pendingListUrlTitle = "";
        sendStopListUrlDiscoveryCommand();
        prefs.edit()
                .putString(KEY_LIST_URL, url)
                .putString(KEY_LIST_URL_TITLE, taskTitle)
                .putString(KEY_LAST_URL, url)
                .apply();
        lastUrl = url;
        setUrlText(url);
        if (uiState != null) {
            uiState.setListUrl(url);
            uiState.setListUrlTitle(taskTitle);
        }
        if (listUrlInput != null) {
            listUrlInput.setText(url);
        }
        sendConfigToPage(false);
        log("已保存课程列表 URL" + (taskTitle.isEmpty() ? "" : "：" + taskTitle));
        restoreOobeAfterListUrlDiscoveryIfNeeded(true);
    }

    private boolean captureDiscoveredListUrlFromNavigation(String url) {
        if (!listUrlDiscoveryRunning || !isDiscoveredListUrl(url)) {
            return false;
        }
        log("URL 获取：捕获任务详情 URL");
        saveDiscoveredListUrl(url, "");
        return true;
    }

    private boolean isDiscoveredListUrl(String url) {
        return url != null && url.contains("student-task-overview") && url.contains("homeworkId=");
    }

    private void loadConfigIntoForm() {
        String username = prefs.getString(KEY_USERNAME, "");
        String password = prefs.getString(KEY_PASSWORD, "");
        String listUrl = prefs.getString(KEY_LIST_URL, "");
        String listUrlTitle = prefs.getString(KEY_LIST_URL_TITLE, "");
        String mode = MODE_VIDEO;
        int dayToStartOn = prefs.getInt(KEY_DAY_TO_START_ON, 1);
        boolean chooseCorrectly = true;
        String reportId = "";
        boolean autoFillLogin = prefs.getBoolean(KEY_AUTO_FILL_LOGIN, true);
        boolean autoSubmitLogin = prefs.getBoolean(KEY_AUTO_SUBMIT_LOGIN, true);
        boolean desktopMode = prefs.getBoolean(KEY_DESKTOP_MODE, true);
        boolean backgroundKeepAlive = prefs.getBoolean(KEY_BACKGROUND_KEEP_ALIVE, true);

        if (uiState != null) {
            uiState.setUsername(username);
            uiState.setPassword(password);
            uiState.setListUrl(listUrl);
            uiState.setListUrlTitle(listUrlTitle);
            uiState.setMode(mode);
            uiState.setDayToStartOn(String.valueOf(dayToStartOn));
            uiState.setChooseCorrectly(chooseCorrectly);
            uiState.setReportId(reportId);
            uiState.setAutoFillLogin(autoFillLogin);
            uiState.setAutoSubmitLogin(autoSubmitLogin);
            uiState.setDesktopMode(desktopMode);
            uiState.setBackgroundKeepAlive(backgroundKeepAlive);
            uiState.setNotificationPermissionGranted(hasNotificationPermission());
        }

        if (usernameInput == null) {
            return;
        }
        usernameInput.setText(username);
        passwordInput.setText(password);
        listUrlInput.setText(listUrl);
        videoModeButton.setChecked(true);
        if (paperModeButton != null) {
            paperModeButton.setChecked(false);
        }
        dayInput.setText(String.valueOf(dayToStartOn));
        chooseCorrectlyCheck.setChecked(chooseCorrectly);
        reportIdInput.setText(reportId);
        autoFillLoginCheck.setChecked(autoFillLogin);
        autoSubmitLoginCheck.setChecked(autoSubmitLogin);
        desktopModeCheck.setChecked(desktopMode);
        if (backgroundKeepAliveCheck != null) {
            backgroundKeepAliveCheck.setChecked(backgroundKeepAlive);
        }
    }

    private void saveConfigFromForm(boolean silent) {
        boolean desktopModeBefore = prefs.getBoolean(KEY_DESKTOP_MODE, true);
        String previousListUrl = prefs.getString(KEY_LIST_URL, "");
        String previousListUrlTitle = prefs.getString(KEY_LIST_URL_TITLE, "");
        String username;
        String password;
        String listUrl;
        String mode;
        int dayToStartOn;
        boolean chooseCorrectly;
        String reportId;
        boolean autoFillLogin;
        boolean autoSubmitLogin;
        boolean desktopMode;
        boolean backgroundKeepAlive;
        if (uiState != null) {
            username = uiState.getUsername().trim();
            password = uiState.getPassword();
            listUrl = normalizeOptionalUrl(uiState.getListUrl());
            mode = MODE_VIDEO;
            dayToStartOn = parsePositiveInt(uiState.getDayToStartOn(), 1);
            chooseCorrectly = true;
            reportId = "";
            autoFillLogin = uiState.getAutoFillLogin();
            autoSubmitLogin = uiState.getAutoSubmitLogin();
            desktopMode = uiState.getDesktopMode();
            backgroundKeepAlive = uiState.getBackgroundKeepAlive();
        } else {
            username = usernameInput.getText().toString().trim();
            password = passwordInput.getText().toString();
            listUrl = normalizeOptionalUrl(listUrlInput.getText().toString());
            mode = MODE_VIDEO;
            dayToStartOn = parsePositiveInt(dayInput.getText().toString(), 1);
            chooseCorrectly = true;
            reportId = "";
            autoFillLogin = autoFillLoginCheck.isChecked();
            autoSubmitLogin = autoSubmitLoginCheck.isChecked();
            desktopMode = desktopModeCheck.isChecked();
            backgroundKeepAlive = backgroundKeepAliveCheck == null || backgroundKeepAliveCheck.isChecked();
        }
        String listUrlTitle = listUrl.equals(previousListUrl) ? previousListUrlTitle : "";

        prefs.edit()
                .putString(KEY_USERNAME, username)
                .putString(KEY_PASSWORD, password)
                .putString(KEY_LIST_URL, listUrl)
                .putString(KEY_LIST_URL_TITLE, listUrlTitle)
                .putString(KEY_MODE, mode)
                .putBoolean(KEY_CHOOSE_CORRECTLY, chooseCorrectly)
                .putString(KEY_REPORT_ID, reportId)
                .putInt(KEY_DAY_TO_START_ON, dayToStartOn)
                .putBoolean(KEY_AUTO_FILL_LOGIN, autoFillLogin)
                .putBoolean(KEY_AUTO_SUBMIT_LOGIN, autoSubmitLogin)
                .putBoolean(KEY_DESKTOP_MODE, desktopMode)
                .putBoolean(KEY_BACKGROUND_KEEP_ALIVE, backgroundKeepAlive)
                .apply();

        if (uiState != null) {
            uiState.setListUrl(listUrl);
            uiState.setListUrlTitle(listUrlTitle);
            uiState.setMode(mode);
            uiState.setDayToStartOn(String.valueOf(dayToStartOn));
            uiState.setBackgroundKeepAlive(backgroundKeepAlive);
        }
        if (listUrlInput != null) {
            listUrlInput.setText(listUrl);
            dayInput.setText(String.valueOf(dayToStartOn));
        }
        updateAutomationButtons();
        syncBackgroundKeepAlive();
        sendConfigToPage(!silent);
        if (!silent) {
            log("配置已保存：模式=刷课");
        }
        if (desktopModeBefore != desktopMode) {
            log("浏览器模式已变更，正在重启内核以应用 UA/viewport 设置");
            restartSession();
        }
    }

    private JSONObject buildConfigJson() throws JSONException {
        JSONObject config = new JSONObject();
        config.put(KEY_USERNAME, prefs.getString(KEY_USERNAME, ""));
        config.put(KEY_PASSWORD, prefs.getString(KEY_PASSWORD, ""));
        config.put(KEY_LIST_URL, prefs.getString(KEY_LIST_URL, ""));
        config.put(KEY_MODE, MODE_VIDEO);
        config.put(KEY_CHOOSE_CORRECTLY, true);
        config.put(KEY_REPORT_ID, "");
        config.put(KEY_DAY_TO_START_ON, prefs.getInt(KEY_DAY_TO_START_ON, 1));
        config.put(KEY_AUTO_FILL_LOGIN, prefs.getBoolean(KEY_AUTO_FILL_LOGIN, true));
        config.put(KEY_AUTO_SUBMIT_LOGIN, prefs.getBoolean(KEY_AUTO_SUBMIT_LOGIN, true));
        config.put(KEY_DESKTOP_MODE, prefs.getBoolean(KEY_DESKTOP_MODE, true));
        config.put(KEY_AUTOMATION_RUNNING, prefs.getBoolean(KEY_AUTOMATION_RUNNING, false));
        config.put(KEY_BACKGROUND_KEEP_ALIVE, prefs.getBoolean(KEY_BACKGROUND_KEEP_ALIVE, true));
        config.put("child_session_active", !parentSessions.isEmpty());
        config.put("child_task_kind", parentSessions.isEmpty() ? "" : childTaskKind);
        return config;
    }

    private String configuredListUrl() {
        return normalizeOptionalUrl(prefs.getString(KEY_LIST_URL, ""));
    }

    private String normalizeUrl(String rawUrl) {
        String url = normalizeOptionalUrl(rawUrl);
        if (url.isEmpty()) {
            return DEFAULT_URL;
        }
        return url;
    }

    private String normalizeOptionalUrl(String rawUrl) {
        String url = rawUrl == null ? "" : rawUrl.trim();
        if (url.isEmpty()) {
            return "";
        }
        if (!url.startsWith("http://") && !url.startsWith("https://") && !url.startsWith("about:")) {
            return "https://" + url;
        }
        return url;
    }

    private int parsePositiveInt(String raw, int fallback) {
        try {
            int value = Integer.parseInt(raw.trim());
            return Math.max(1, value);
        } catch (RuntimeException ignored) {
            return fallback;
        }
    }

    private void setUrlText(String url) {
        if (uiState != null) {
            uiState.setUrl(url);
        }
        if (urlInput != null) {
            urlInput.setText(url);
        }
    }

    private void setLoadProgress(int progress) {
        int clamped = Math.max(0, Math.min(100, progress));
        if (uiState != null) {
            uiState.setLoadProgress(clamped);
        }
        if (progressBar != null) {
            progressBar.setProgress(clamped);
        }
    }

    private void status(String message) {
        if (uiState != null) {
            uiState.setStatus(message);
        }
        if (statusText != null) {
            statusText.setText(message);
        }
        log(message);
    }

    private void log(String message) {
        String time = new SimpleDateFormat("HH:mm:ss", Locale.ROOT).format(new Date());
        String line = "[" + time + "] " + message;
        Log.d("AutoEwt", line);
        logBuffer.append(line).append('\n');
        int maxLogChars = 16000;
        if (logBuffer.length() > maxLogChars) {
            logBuffer.delete(0, logBuffer.length() - maxLogChars);
        }
        if (uiState != null) {
            uiState.setSingleLogLine(line);
            uiState.setFullLog(logBuffer.toString());
        }
        if (logSingleLineText != null) {
            logSingleLineText.setText(line);
        }
        if (logText == null) {
            return;
        }
        logText.append(line + "\n");
        if (logMode == LOG_MODE_FULL && logScrollView != null) {
            logScrollView.post(() -> logScrollView.fullScroll(View.FOCUS_DOWN));
        }
    }

    private int dp(int value) {
        float density = getResources().getDisplayMetrics().density;
        return Math.round(value * density);
    }

    private class AutoEwtMessageDelegate implements WebExtension.MessageDelegate {
        @Override
        public void onConnect(WebExtension.Port port) {
            activePort = port;
            if (!connectedPorts.contains(port)) {
                connectedPorts.add(port);
            }
            port.setDelegate(new WebExtension.PortDelegate() {
                @Override
                public void onPortMessage(Object message, WebExtension.Port port) {
                    activePort = port;
                    if (!connectedPorts.contains(port)) {
                        connectedPorts.add(port);
                    }
                    handleExtensionMessage(message);
                }

                @Override
                public void onDisconnect(WebExtension.Port port) {
                    connectedPorts.remove(port);
                    if (activePort == port) {
                        activePort = null;
                    }
                    log("WebExtension port disconnected");
                }
            });

            JSONObject hello = new JSONObject();
            try {
                hello.put("type", "hello");
                hello.put("nativeReady", true);
                hello.put("at", System.currentTimeMillis());
            } catch (JSONException ignored) {
            }
            port.postMessage(hello);
            sendConfigToPage(false);
            sendListUrlDiscoveryCommand();
            log("WebExtension port connected: " + port.name);
        }

        @Override
        public GeckoResult<Object> onMessage(
                String nativeApp,
                Object message,
                WebExtension.MessageSender sender
        ) {
            handleExtensionMessage(message);

            JSONObject reply = new JSONObject();
            try {
                reply.put("ok", true);
                reply.put("receivedAt", System.currentTimeMillis());
            } catch (JSONException ignored) {
            }
            return GeckoResult.fromValue(reply);
        }
    }

    private void handleExtensionMessage(Object message) {
        if (message instanceof JSONObject) {
            JSONObject json = (JSONObject) message;
            String type = json.optString("type", "message");
            if ("pageState".equals(type)) {
                String title = json.optString("title", "");
                String url = json.optString("url", "");
                String reason = json.optString("reason", "");
                int videoCount = json.optInt("videoCount", 0);
                double videoProgress = json.optDouble("videoProgress", -1);
                int courseCandidateCount = json.optInt("courseCandidateCount", -1);
                String courseCandidateSummary = json.optString("courseCandidateSummary", "");
                if (!url.isEmpty()) {
                    lastUrl = url;
                    if (shouldPersistUrl(url)) {
                        prefs.edit().putString(KEY_LAST_URL, url).apply();
                    }
                    setUrlText(url);
                }
                if (!"probe".equals(reason)) {
                    return;
                }
                log("页面：" + title + " | videos=" + videoCount + " | progress=" + videoProgress
                        + (courseCandidateCount >= 0 ? " | 课程入口=" + courseCandidateCount : ""));
                if (!courseCandidateSummary.isEmpty()) {
                    log("课程入口：" + courseCandidateSummary);
                }
            } else if ("loginFill".equals(type)) {
                boolean usernameFilled = json.optBoolean("usernameFilled", false);
                boolean passwordFilled = json.optBoolean("passwordFilled", false);
                boolean submitted = json.optBoolean("submitted", false);
                boolean captcha = json.optBoolean("captchaDetected", false);
                String loginError = json.optString("loginError", "");
                log("登录填充：账号=" + usernameFilled + " 密码=" + passwordFilled
                        + " 提交=" + submitted + " 验证码=" + captcha
                        + (loginError.isEmpty() ? "" : " 错误=" + loginError));
                if (!loginError.isEmpty()) {
                    prefs.edit().putBoolean(KEY_AUTOMATION_RUNNING, false).apply();
                    listUrlDiscoveryRunning = false;
                    listUrlCandidateSelectionPending = false;
                    String loginFailure = cleanLoginFailureMessage(loginError);
                    setListUrlDiscoveryUi(false, loginFailure);
                    setListUrlManualEntryVisible(false);
                    if (uiState != null) {
                        uiState.setOobeStep(2);
                        uiState.setStatus(loginFailure);
                    }
                    updateAutomationButtons();
                    restoreOobeAfterListUrlDiscoveryIfNeeded(false);
                }
            } else if ("automationLog".equals(type)) {
                String messageText = json.optString("message", "");
                String step = json.optString("step", "");
                String label = json.optString("label", "");
                if (!label.isEmpty()) {
                    messageText = messageText + "：" + label;
                }
                log("自动化[" + step + "]：" + messageText);
                if (messageText.contains("验证码") || messageText.contains("缺少")) {
                    prefs.edit().putBoolean(KEY_AUTOMATION_RUNNING, false).apply();
                    updateAutomationButtons();
                }
            } else if ("automationProgress".equals(type)) {
                double progress = json.optDouble("progress", -1);
                double currentTime = json.optDouble("currentTime", 0);
                double duration = json.optDouble("duration", 0);
                boolean paused = json.optBoolean("paused", false);
                boolean ended = json.optBoolean("ended", false);
                if (progress >= 0) {
                    setLoadProgress((int) Math.round(progress * 100));
                }
                String videoStatus = "视频进度：" + formatSeconds(currentTime)
                        + " / " + formatSeconds(duration)
                        + (paused ? "，已暂停" : "")
                        + (ended ? "，已结束" : "");
                if (uiState != null) {
                    uiState.setVideoStatus(videoStatus);
                    uiState.setStatus(videoStatus);
                }
                if (statusText != null) {
                    statusText.setText(videoStatus);
                }
            } else if ("automationDayProgress".equals(type)) {
                int day = json.optInt("day", 0);
                int totalDays = json.optInt("totalDays", 0);
                if (uiState != null && day > 0 && totalDays > 0) {
                    uiState.updateCourseDayProgress(day, totalDays);
                }
            } else if ("automationTotalProgress".equals(type)) {
                int done = json.optInt("done", 0);
                int total = json.optInt("total", 0);
                String scope = json.optString("scope", "");
                if (uiState != null && total > 0) {
                    uiState.updateTotalCourseProgress(done, total, scope);
                }
            } else if ("automationFinished".equals(type)) {
                String messageText = json.optString("message", "自动化已完成");
                log("自动化完成：" + messageText);
                stopAutomation();
                if (uiState != null) {
                    uiState.setStatus(messageText);
                }
            } else if ("nativeTap".equals(type)) {
                handleNativeTap(json);
            } else if ("closeChildSession".equals(type)) {
                closeChildSession(json.optString("reason", "page"));
            } else if ("restartBrowser".equals(type)) {
                restartBrowserFromAutomation(json.optString("reason", "unknown"), json.optString("url", ""));
            } else if ("listUrlDiscoveryLog".equals(type)) {
                String messageText = json.optString("message", "");
                setListUrlDiscoveryUi(true, messageText);
                log("URL 获取：" + messageText);
            } else if ("listUrlDiscoveryCandidates".equals(type)) {
                if (listUrlCandidateSelectionPending) {
                    setListUrlDiscoveryUi(true, "正在打开所选任务：" + pendingListUrlTitle);
                    log("URL 获取：已选择任务，忽略重复候选列表");
                    return;
                }
                setListUrlDiscoveryUi(true, "请选择要保存的任务");
                log("URL 获取：请选择要保存的任务");
                runOnUiThread(() -> showListUrlCandidateDialog(json.optJSONArray("candidates")));
            } else if ("listUrlDiscovered".equals(type)) {
                saveDiscoveredListUrl(json.optString("url", ""), json.optString("title", ""));
            } else if ("listUrlDiscoveryFailed".equals(type)) {
                String reason = cleanLoginFailureMessage(json.optString("reason", "未找到可用任务"));
                listUrlDiscoveryRunning = false;
                listUrlCandidateSelectionPending = false;
                pendingListUrlTitle = "";
                setListUrlDiscoveryUi(false, reason);
                boolean allowManualFallback = !reason.contains("登录失败")
                        && !reason.contains("账号")
                        && !reason.contains("密码")
                        && !reason.contains("试卷/测验");
                setListUrlManualEntryVisible(allowManualFallback);
                if (uiState != null) {
                    uiState.setOobeStep(2);
                }
                log("URL 获取失败：" + reason);
                restoreOobeAfterListUrlDiscoveryIfNeeded(false);
            } else {
                log("扩展消息：" + json);
            }
        } else {
            log("扩展消息：" + message);
        }
    }

    private void closeChildSession(String reason) {
        if (parentSessions.isEmpty()) {
            String listUrl = configuredListUrl();
            if (prefs.getBoolean(KEY_AUTOMATION_RUNNING, false) && !listUrl.isEmpty()) {
                log("当前没有父窗口，直接返回课程列表：" + reason);
                load(listUrl);
                sendAutomationCommand("start");
            } else {
                log("当前没有子窗口可关闭：" + reason);
            }
            return;
        }
        GeckoSession child = session;
        GeckoSession parent = parentSessions.pop();
        session = parent;
        childTaskKind = "";
        activePort = null;
        geckoView.setSession(parent);
        try {
            child.close();
        } catch (RuntimeException ignored) {
        }
        attachBridgeToSession(parent);
        notifyChildSessionClosed(reason);
        if (prefs.getBoolean(KEY_AUTOMATION_RUNNING, false)) {
            sendAutomationCommand("start");
        }
        log("已关闭课程窗口并返回任务列表：" + reason);
    }

    private void closeAllChildSessions(String reason) {
        while (!parentSessions.isEmpty()) {
            GeckoSession child = session;
            GeckoSession parent = parentSessions.pop();
            session = parent;
            childTaskKind = "";
            activePort = null;
            geckoView.setSession(parent);
            try {
                child.close();
            } catch (RuntimeException ignored) {
            }
            attachBridgeToSession(parent);
        }
        pendingChildTaskKind = "";
        childTaskKind = "";
        notifyChildSessionClosed(reason);
    }

    private void notifyChildSessionClosed(String reason) {
        JSONObject message = new JSONObject();
        try {
            message.put("type", "childSessionClosed");
            message.put("reason", reason);
            message.put("automationRunning", prefs.getBoolean(KEY_AUTOMATION_RUNNING, false));
            message.put("at", System.currentTimeMillis());
        } catch (JSONException ignored) {
        }
        postToConnectedPorts(message);
    }

    private void notifyChildSessionOpened(String uri) {
        JSONObject message = new JSONObject();
        try {
            message.put("type", "childSessionOpened");
            message.put("uri", uri == null ? "" : uri);
            message.put("taskKind", childTaskKind);
            message.put("automationRunning", prefs.getBoolean(KEY_AUTOMATION_RUNNING, false));
            message.put("at", System.currentTimeMillis());
        } catch (JSONException ignored) {
        }
        postToConnectedPorts(message);
    }

    private void handleNativeTap(JSONObject json) {
        if (geckoView == null) {
            return;
        }
        double clientX = json.optDouble("clientX", Double.NaN);
        double clientY = json.optDouble("clientY", Double.NaN);
        double viewportWidth = json.optDouble("viewportWidth", 0);
        double viewportHeight = json.optDouble("viewportHeight", 0);
        String label = json.optString("label", "课程入口");
        if ("course".equals(json.optString("reason", ""))) {
            String taskKind = json.optString("taskKind", "");
            if (!taskKind.isEmpty()) {
                pendingChildTaskKind = taskKind;
            }
        }
        if (!Double.isFinite(clientX) || !Double.isFinite(clientY) || viewportWidth <= 0 || viewportHeight <= 0) {
            log("原生点击坐标无效：" + label
                    + " client=" + clientX + "," + clientY
                    + " viewport=" + viewportWidth + "x" + viewportHeight);
            return;
        }
        boolean browserWasHidden = uiState != null && !uiState.getBrowserVisible();
        if (browserWasHidden) {
            uiState.setBrowserVisible(true);
            log("需要原生点击，已临时显示浏览器");
        }
        dispatchNativeTapWhenReady(clientX, clientY, viewportWidth, viewportHeight, label, browserWasHidden ? 1 : 0);
    }

    private void dispatchNativeTapWhenReady(
            double clientX,
            double clientY,
            double viewportWidth,
            double viewportHeight,
            String label,
            int attempt
    ) {
        int delayMs = attempt <= 0 ? 0 : 160;
        geckoView.postDelayed(() -> {
            int viewWidth = geckoView.getWidth();
            int viewHeight = geckoView.getHeight();
            if (viewWidth < 32 || viewHeight < 32) {
                if (uiState != null && !uiState.getBrowserVisible()) {
                    uiState.setBrowserVisible(true);
                }
                if (attempt < 12) {
                    if (attempt == 1) {
                        log("等待浏览器恢复布局后点击：" + label);
                    }
                    dispatchNativeTapWhenReady(clientX, clientY, viewportWidth, viewportHeight, label, attempt + 1);
                } else {
                    log("原生点击失败：浏览器视图仍处于隐藏布局：" + label);
                }
                return;
            }
            float scaledX = (float) (clientX / viewportWidth * viewWidth);
            float scaledY = (float) (clientY / viewportHeight * viewHeight);
            float x = clamp(scaledX, 1, viewWidth - 1);
            float y = clamp(scaledY, 1, viewHeight - 1);
            dispatchTapToGeckoView(x, y, label);
        }, delayMs);
    }

    private void dispatchTapToGeckoView(float x, float y, String label) {
        long downTime = SystemClock.uptimeMillis();
        MotionEvent down = MotionEvent.obtain(downTime, downTime, MotionEvent.ACTION_DOWN, x, y, 0);
        down.setSource(InputDevice.SOURCE_TOUCHSCREEN);
        geckoView.dispatchTouchEvent(down);
        down.recycle();

        geckoView.postDelayed(() -> {
            long upTime = SystemClock.uptimeMillis();
            MotionEvent up = MotionEvent.obtain(downTime, upTime, MotionEvent.ACTION_UP, x, y, 0);
            up.setSource(InputDevice.SOURCE_TOUCHSCREEN);
            geckoView.dispatchTouchEvent(up);
            up.recycle();
            log("原生点击：" + label + " @ " + Math.round(x) + "," + Math.round(y));
        }, 80);
    }

    private float clamp(float value, float min, float max) {
        if (max < min) {
            return min;
        }
        return Math.max(min, Math.min(max, value));
    }

    private String formatSeconds(double rawSeconds) {
        int seconds = Math.max(0, (int) Math.round(rawSeconds));
        int minutes = seconds / 60;
        int rest = seconds % 60;
        return String.format(Locale.ROOT, "%02d:%02d", minutes, rest);
    }

    private static class FlowLayout extends ViewGroup {
        private int itemSpacing;
        private int lineSpacing;

        FlowLayout(android.content.Context context) {
            super(context);
        }

        void setItemSpacing(int itemSpacing) {
            this.itemSpacing = itemSpacing;
        }

        void setLineSpacing(int lineSpacing) {
            this.lineSpacing = lineSpacing;
        }

        @Override
        protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
            int widthMode = MeasureSpec.getMode(widthMeasureSpec);
            int widthSize = MeasureSpec.getSize(widthMeasureSpec);
            int maxWidth = widthMode == MeasureSpec.UNSPECIFIED
                    ? Integer.MAX_VALUE
                    : Math.max(0, widthSize - getPaddingLeft() - getPaddingRight());
            int lineWidth = 0;
            int lineHeight = 0;
            int measuredWidth = 0;
            int measuredHeight = getPaddingTop() + getPaddingBottom();

            for (int i = 0; i < getChildCount(); i += 1) {
                View child = getChildAt(i);
                if (child.getVisibility() == GONE) {
                    continue;
                }
                measureChildWithMargins(child, widthMeasureSpec, 0, heightMeasureSpec, 0);
                MarginLayoutParams lp = (MarginLayoutParams) child.getLayoutParams();
                int childWidth = child.getMeasuredWidth() + lp.leftMargin + lp.rightMargin;
                int childHeight = child.getMeasuredHeight() + lp.topMargin + lp.bottomMargin;
                int nextWidth = lineWidth == 0 ? childWidth : lineWidth + itemSpacing + childWidth;
                if (nextWidth > maxWidth && lineWidth > 0) {
                    measuredWidth = Math.max(measuredWidth, lineWidth);
                    measuredHeight += lineHeight + lineSpacing;
                    lineWidth = childWidth;
                    lineHeight = childHeight;
                } else {
                    lineWidth = nextWidth;
                    lineHeight = Math.max(lineHeight, childHeight);
                }
            }

            measuredWidth = Math.max(measuredWidth, lineWidth) + getPaddingLeft() + getPaddingRight();
            measuredHeight += lineHeight;
            int resolvedWidth = widthMode == MeasureSpec.EXACTLY ? widthSize : measuredWidth;
            int resolvedHeight = resolveSize(measuredHeight, heightMeasureSpec);
            setMeasuredDimension(resolvedWidth, resolvedHeight);
        }

        @Override
        protected void onLayout(boolean changed, int left, int top, int right, int bottom) {
            int maxWidth = Math.max(0, right - left - getPaddingLeft() - getPaddingRight());
            int x = getPaddingLeft();
            int y = getPaddingTop();
            int lineHeight = 0;

            for (int i = 0; i < getChildCount(); i += 1) {
                View child = getChildAt(i);
                if (child.getVisibility() == GONE) {
                    continue;
                }
                MarginLayoutParams lp = (MarginLayoutParams) child.getLayoutParams();
                int childWidth = child.getMeasuredWidth();
                int childHeight = child.getMeasuredHeight();
                int nextWidth = x == getPaddingLeft()
                        ? childWidth + lp.leftMargin + lp.rightMargin
                        : x - getPaddingLeft() + itemSpacing + childWidth + lp.leftMargin + lp.rightMargin;
                if (nextWidth > maxWidth && x > getPaddingLeft()) {
                    x = getPaddingLeft();
                    y += lineHeight + lineSpacing;
                    lineHeight = 0;
                }
                int childLeft = x + lp.leftMargin;
                int childTop = y + lp.topMargin;
                child.layout(childLeft, childTop, childLeft + childWidth, childTop + childHeight);
                x = childLeft + childWidth + lp.rightMargin + itemSpacing;
                lineHeight = Math.max(lineHeight, childHeight + lp.topMargin + lp.bottomMargin);
            }
        }

        @Override
        protected LayoutParams generateDefaultLayoutParams() {
            return new MarginLayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT);
        }

        @Override
        protected LayoutParams generateLayoutParams(LayoutParams params) {
            return new MarginLayoutParams(params);
        }

        @Override
        public LayoutParams generateLayoutParams(android.util.AttributeSet attrs) {
            return new MarginLayoutParams(getContext(), attrs);
        }

        @Override
        protected boolean checkLayoutParams(LayoutParams params) {
            return params instanceof MarginLayoutParams;
        }
    }
}
