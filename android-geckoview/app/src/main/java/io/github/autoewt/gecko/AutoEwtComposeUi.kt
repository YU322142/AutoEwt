package io.github.autoewt.gecko

import android.view.ViewGroup
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.PrimaryTabRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import kotlinx.coroutines.launch
import org.mozilla.geckoview.GeckoView

class AutoEwtUiState {
    companion object {
        const val PAGE_BROWSER = 0
        const val PAGE_CONFIG = 1
        const val LOG_MODE_FULL = 0
        const val LOG_MODE_SINGLE = 1
        const val LOG_MODE_HIDDEN = 2
    }

    var page by mutableIntStateOf(PAGE_BROWSER)
    var browserVisible by mutableStateOf(true)
    var url by mutableStateOf("")
    var status by mutableStateOf("就绪")
    var loadProgress by mutableIntStateOf(0)
    var videoStatus by mutableStateOf("")
    var automationRunning by mutableStateOf(false)
    var logMode by mutableIntStateOf(LOG_MODE_SINGLE)
    var singleLogLine by mutableStateOf("")
    var fullLog by mutableStateOf("")

    var username by mutableStateOf("")
    var password by mutableStateOf("")
    var listUrl by mutableStateOf("")
    var mode by mutableStateOf("video")
    var dayToStartOn by mutableStateOf("1")
    var chooseCorrectly by mutableStateOf(true)
    var reportId by mutableStateOf("")
    var autoFillLogin by mutableStateOf(true)
    var autoSubmitLogin by mutableStateOf(true)
    var desktopMode by mutableStateOf(true)
}

interface AutoEwtUiController {
    val state: AutoEwtUiState

    fun geckoView(): GeckoView
    fun showBrowserPage()
    fun showConfigPage()
    fun setBrowserVisibleFromUi(visible: Boolean)
    fun cycleLogModeFromUi()
    fun openUrlFromUi(url: String)
    fun openConfiguredCourseFromUi()
    fun startAutomationFromUi()
    fun stopAutomationFromUi()
    fun requestFillLoginFromUi()
    fun requestProbeFromUi()
    fun restartSessionFromUi()
    fun saveConfigFromUi()
    fun saveAndOpenFromUi()
}

object AutoEwtComposeUi {
    @JvmStatic
    fun install(activity: ComponentActivity, controller: AutoEwtUiController) {
        activity.setContent {
            AutoEwtTheme {
                AutoEwtApp(controller)
            }
        }
    }
}

@Composable
private fun AutoEwtTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = androidx.compose.material3.lightColorScheme(
            primary = Color(0xFF0F6CBD),
            onPrimary = Color.White,
            secondary = Color(0xFF4D5F2F),
            tertiary = Color(0xFF8C4A2F),
            surface = Color(0xFFFBFCFE),
            surfaceVariant = Color(0xFFE8EEF5),
            background = Color(0xFFF6F8FB),
            outline = Color(0xFFB8C1CC)
        ),
        content = content
    )
}

@Composable
private fun AutoEwtApp(controller: AutoEwtUiController) {
    val state = controller.state
    Surface(
        modifier = Modifier.fillMaxSize(),
        color = MaterialTheme.colorScheme.background
    ) {
        Column(modifier = Modifier.fillMaxSize()) {
            AppHeader(controller)
            PrimaryTabRow(selectedTabIndex = state.page) {
                Tab(
                    selected = state.page == AutoEwtUiState.PAGE_BROWSER,
                    onClick = controller::showBrowserPage,
                    text = { Text("浏览器") }
                )
                Tab(
                    selected = state.page == AutoEwtUiState.PAGE_CONFIG,
                    onClick = controller::showConfigPage,
                    text = { Text("配置") }
                )
            }
            when (state.page) {
                AutoEwtUiState.PAGE_CONFIG -> ConfigScreen(controller)
                else -> BrowserScreen(controller)
            }
        }
    }
}

@Composable
private fun AppHeader(controller: AutoEwtUiController) {
    val state = controller.state
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(56.dp)
            .padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = "AutoEwt Gecko",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
            Text(
                text = state.status,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
        Text(
            text = if (state.automationRunning) "运行中" else "待机",
            style = MaterialTheme.typography.labelLarge,
            color = if (state.automationRunning) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun BrowserScreen(controller: AutoEwtUiController) {
    BoxWithConstraints(modifier = Modifier.fillMaxSize()) {
        val wide = maxWidth >= 600.dp
        if (wide) {
            Row(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(12.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                BrowserControlColumn(
                    controller = controller,
                    modifier = Modifier
                        .widthIn(min = 320.dp, max = 420.dp)
                        .fillMaxHeight()
                )
                BrowserWorkspace(
                    controller = controller,
                    modifier = Modifier.weight(1f)
                )
            }
        } else {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(horizontal = 10.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                BrowserControlColumn(
                    controller = controller,
                    compact = true,
                    modifier = Modifier.fillMaxWidth()
                )
                BrowserWorkspace(
                    controller = controller,
                    modifier = Modifier.weight(1f)
                )
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
private fun BrowserControlColumn(
    controller: AutoEwtUiController,
    modifier: Modifier = Modifier,
    compact: Boolean = false
) {
    val state = controller.state
    Column(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surface)
            .padding(10.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            OutlinedTextField(
                value = state.url,
                onValueChange = { state.url = it },
                modifier = Modifier.weight(1f),
                singleLine = true,
                label = { Text("地址") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri)
            )
            Button(
                onClick = { controller.openUrlFromUi(state.url) },
                modifier = Modifier.height(56.dp),
                contentPadding = ButtonDefaults.ButtonWithIconContentPadding
            ) {
                ActionIcon(R.drawable.ic_open)
                Spacer(Modifier.width(6.dp))
                Text("打开")
            }
        }

        if (compact) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                BrowserActionButtons(controller, state)
            }
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                BrowserViewAndLogChips(controller, state)
            }
        } else {
            FlowRow(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                BrowserActionButtons(controller, state)
            }

            FlowRow(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                BrowserViewAndLogChips(controller, state)
            }
        }

        LinearProgressIndicator(
            progress = { (state.loadProgress.coerceIn(0, 100) / 100f) },
            modifier = Modifier.fillMaxWidth()
        )

        if (state.videoStatus.isNotBlank()) {
            Text(
                text = state.videoStatus,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium,
                color = MaterialTheme.colorScheme.onSurface
            )
        }

        if (compact) {
            LogPanel(state, modifier = Modifier.fillMaxWidth())
        } else {
            Spacer(Modifier.height(2.dp))
            LogPanel(
                state = state,
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f, fill = false)
            )
        }
    }
}

@Composable
private fun BrowserActionButtons(controller: AutoEwtUiController, state: AutoEwtUiState) {
    ActionButton(R.drawable.ic_course, "打开课程", onClick = controller::openConfiguredCourseFromUi)
    ActionButton(
        R.drawable.ic_play,
        "开始刷课",
        enabled = !state.automationRunning,
        primary = true,
        onClick = controller::startAutomationFromUi
    )
    ActionButton(
        R.drawable.ic_stop,
        "停止",
        enabled = state.automationRunning,
        destructive = true,
        onClick = controller::stopAutomationFromUi
    )
    ActionButton(R.drawable.ic_login, "填登录", onClick = controller::requestFillLoginFromUi)
    ActionButton(R.drawable.ic_probe, "探测", onClick = controller::requestProbeFromUi)
    ActionButton(R.drawable.ic_restart, "重启", onClick = controller::restartSessionFromUi)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun BrowserViewAndLogChips(controller: AutoEwtUiController, state: AutoEwtUiState) {
    FilterChip(
        selected = state.browserVisible,
        onClick = { controller.setBrowserVisibleFromUi(!state.browserVisible) },
        label = { Text(if (state.browserVisible) "浏览器显示" else "浏览器隐藏") },
        leadingIcon = { ActionIcon(R.drawable.ic_open) }
    )
    LogModeChip(
        label = "完整",
        icon = R.drawable.ic_log_full,
        selected = state.logMode == AutoEwtUiState.LOG_MODE_FULL,
        onClick = { setLogMode(controller, AutoEwtUiState.LOG_MODE_FULL) }
    )
    LogModeChip(
        label = "单行",
        icon = R.drawable.ic_log_single,
        selected = state.logMode == AutoEwtUiState.LOG_MODE_SINGLE,
        onClick = { setLogMode(controller, AutoEwtUiState.LOG_MODE_SINGLE) }
    )
    LogModeChip(
        label = "隐藏",
        icon = R.drawable.ic_log_hidden,
        selected = state.logMode == AutoEwtUiState.LOG_MODE_HIDDEN,
        onClick = { setLogMode(controller, AutoEwtUiState.LOG_MODE_HIDDEN) }
    )
}

@Composable
private fun BrowserWorkspace(controller: AutoEwtUiController, modifier: Modifier = Modifier) {
    val state = controller.state
    Column(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surface)
            .padding(8.dp)
    ) {
        if (state.browserVisible) {
            BrowserView(controller, Modifier.fillMaxSize())
        } else {
            Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    text = "浏览器已隐藏",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}

@Composable
private fun BrowserView(controller: AutoEwtUiController, modifier: Modifier = Modifier) {
    AndroidView(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(Color.White),
        factory = {
            controller.geckoView().also { view ->
                (view.parent as? ViewGroup)?.removeView(view)
            }
        }
    )
}

@Composable
private fun LogPanel(state: AutoEwtUiState, modifier: Modifier = Modifier) {
    when (state.logMode) {
        AutoEwtUiState.LOG_MODE_FULL -> {
            val scroll = rememberScrollState()
            val scope = rememberCoroutineScope()
            var followTail by remember { mutableStateOf(true) }
            val atBottom by remember {
                derivedStateOf { scroll.maxValue == 0 || scroll.value >= scroll.maxValue - 6 }
            }
            LaunchedEffect(scroll.value, scroll.maxValue) {
                if (scroll.isScrollInProgress || atBottom) {
                    followTail = atBottom
                }
            }
            LaunchedEffect(state.fullLog, scroll.maxValue) {
                if (followTail) {
                    scroll.scrollTo(scroll.maxValue)
                }
            }
            Box(
                modifier = modifier
                    .height(170.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(MaterialTheme.colorScheme.surfaceVariant)
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .verticalScroll(scroll)
                        .padding(start = 10.dp, top = 10.dp, end = 10.dp, bottom = 48.dp)
                ) {
                    Text(
                        text = state.fullLog.ifBlank { "暂无日志" },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                if (!atBottom) {
                    IconButton(
                        onClick = {
                            followTail = true
                            scope.launch {
                                scroll.animateScrollTo(scroll.maxValue)
                            }
                        },
                        modifier = Modifier
                            .size(44.dp)
                            .align(Alignment.BottomEnd)
                            .padding(6.dp)
                            .clip(RoundedCornerShape(22.dp))
                            .background(MaterialTheme.colorScheme.surface)
                    ) {
                        Icon(
                            painter = painterResource(R.drawable.ic_arrow_down),
                            contentDescription = "跳到最新日志",
                            modifier = Modifier.size(20.dp),
                            tint = MaterialTheme.colorScheme.primary
                        )
                    }
                }
            }
        }
        AutoEwtUiState.LOG_MODE_SINGLE -> {
            Text(
                text = state.singleLogLine.ifBlank { "暂无日志" },
                modifier = modifier
                    .clip(RoundedCornerShape(8.dp))
                    .background(MaterialTheme.colorScheme.surfaceVariant)
                    .padding(horizontal = 10.dp, vertical = 8.dp),
                style = MaterialTheme.typography.bodySmall,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}

@Composable
private fun ConfigScreen(controller: AutoEwtUiController) {
    BoxWithConstraints(modifier = Modifier.fillMaxSize()) {
        val wide = maxWidth >= 600.dp
        if (wide) {
            Row(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(12.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                ConfigIdentityPanel(controller, Modifier.weight(1f))
                ConfigRunPanel(controller, Modifier.weight(1f))
            }
        } else {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .verticalScroll(rememberScrollState())
                    .padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                ConfigIdentityPanel(controller)
                ConfigRunPanel(controller)
            }
        }
    }
}

@Composable
private fun ConfigIdentityPanel(controller: AutoEwtUiController, modifier: Modifier = Modifier) {
    val state = controller.state
    Panel(modifier = modifier) {
        SectionTitle("账号与课程")
        OutlinedTextField(
            value = state.username,
            onValueChange = { state.username = it },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            label = { Text("账号") }
        )
        OutlinedTextField(
            value = state.password,
            onValueChange = { state.password = it },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            label = { Text("密码") },
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password)
        )
        OutlinedTextField(
            value = state.listUrl,
            onValueChange = { state.listUrl = it },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            label = { Text("课程列表 URL") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri)
        )
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Button(onClick = controller::saveConfigFromUi) {
                Text("保存配置")
            }
            OutlinedButton(onClick = controller::saveAndOpenFromUi) {
                Text("保存并打开")
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
private fun ConfigRunPanel(controller: AutoEwtUiController, modifier: Modifier = Modifier) {
    val state = controller.state
    Panel(modifier = modifier) {
        SectionTitle("运行模式")
        FlowRow(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp)
        ) {
            FilterChip(
                selected = state.mode == "video",
                onClick = { state.mode = "video" },
                label = { Text("刷课") },
                leadingIcon = { ActionIcon(R.drawable.ic_play) }
            )
            FilterChip(
                selected = state.mode == "paper",
                onClick = { state.mode = "paper" },
                label = { Text("做题") },
                leadingIcon = { ActionIcon(R.drawable.ic_course) }
            )
        }
        OutlinedTextField(
            value = state.dayToStartOn,
            onValueChange = { state.dayToStartOn = it.filter(Char::isDigit) },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            label = { Text("从第几天开始") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number)
        )
        CheckRow(
            checked = state.chooseCorrectly,
            onCheckedChange = { state.chooseCorrectly = it },
            text = "做题时选择正确答案"
        )
        OutlinedTextField(
            value = state.reportId,
            onValueChange = { state.reportId = it },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            label = { Text("report_id") }
        )
        HorizontalDivider()
        SectionTitle("浏览器兼容")
        CheckRow(
            checked = state.autoFillLogin,
            onCheckedChange = { state.autoFillLogin = it },
            text = "自动填入账号密码"
        )
        CheckRow(
            checked = state.autoSubmitLogin,
            onCheckedChange = { state.autoSubmitLogin = it },
            text = "填入后自动登录"
        )
        CheckRow(
            checked = state.desktopMode,
            onCheckedChange = { state.desktopMode = it },
            text = "桌面浏览器模式"
        )
    }
}

@Composable
private fun Panel(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surface)
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
        content = content
    )
}

@Composable
private fun SectionTitle(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.titleSmall,
        fontWeight = FontWeight.SemiBold,
        color = MaterialTheme.colorScheme.onSurface
    )
}

@Composable
private fun CheckRow(checked: Boolean, onCheckedChange: (Boolean) -> Unit, text: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Checkbox(checked = checked, onCheckedChange = onCheckedChange)
        Text(
            text = text,
            modifier = Modifier.weight(1f),
            style = MaterialTheme.typography.bodyMedium
        )
    }
}

@Composable
private fun ActionButton(
    icon: Int,
    text: String,
    enabled: Boolean = true,
    primary: Boolean = false,
    destructive: Boolean = false,
    onClick: () -> Unit
) {
    val colors = when {
        destructive -> ButtonDefaults.outlinedButtonColors(
            contentColor = MaterialTheme.colorScheme.error
        )
        primary -> ButtonDefaults.buttonColors()
        else -> ButtonDefaults.filledTonalButtonColors()
    }
    val content: @Composable () -> Unit = {
        ActionIcon(icon)
        Spacer(Modifier.width(6.dp))
        Text(text, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
    if (destructive) {
        OutlinedButton(
            onClick = onClick,
            enabled = enabled,
            contentPadding = ButtonDefaults.ButtonWithIconContentPadding,
            colors = colors,
            modifier = Modifier.widthIn(min = 104.dp)
        ) {
            content()
        }
    } else if (primary) {
        Button(
            onClick = onClick,
            enabled = enabled,
            contentPadding = ButtonDefaults.ButtonWithIconContentPadding,
            modifier = Modifier.widthIn(min = 112.dp)
        ) {
            content()
        }
    } else {
        androidx.compose.material3.FilledTonalButton(
            onClick = onClick,
            enabled = enabled,
            contentPadding = ButtonDefaults.ButtonWithIconContentPadding,
            colors = colors,
            modifier = Modifier.widthIn(min = 104.dp)
        ) {
            content()
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun LogModeChip(label: String, icon: Int, selected: Boolean, onClick: () -> Unit) {
    FilterChip(
        selected = selected,
        onClick = onClick,
        label = { Text(label) },
        leadingIcon = { ActionIcon(icon) }
    )
}

@Composable
private fun ActionIcon(icon: Int) {
    Icon(
        painter = painterResource(icon),
        contentDescription = null,
        modifier = Modifier.size(18.dp)
    )
}

private fun setLogMode(controller: AutoEwtUiController, mode: Int) {
    while (controller.state.logMode != mode) {
        controller.cycleLogModeFromUi()
    }
}
