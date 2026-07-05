package io.github.autoewt.gecko

import android.view.ViewGroup
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.LocalMinimumInteractiveComponentSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedIconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.PrimaryTabRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
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
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import kotlinx.coroutines.launch
import org.mozilla.geckoview.GeckoView

data class AutoEwtListUrlCandidate(
    val id: String,
    val title: String,
    val status: String,
    val filter: String,
    val startTime: String,
    val deadline: String,
    val teacher: String
)

private data class ControlButtonSpec(
    val icon: Int,
    val text: String,
    val enabled: Boolean = true,
    val primary: Boolean = false,
    val destructive: Boolean = false,
    val selected: Boolean = false,
    val onClick: () -> Unit
)

private data class HelpIconSpec(
    val icon: Int,
    val title: String,
    val body: String
)

private val DashboardButtonHeight = 36.dp
private val DashboardIconButtonSize = 32.dp
private val DashboardButtonShape = RoundedCornerShape(8.dp)
private val DashboardButtonPadding = PaddingValues(horizontal = 8.dp, vertical = 0.dp)
private val DashboardButtonGap = 4.dp
private val DashboardButtonMinWidth = 88.dp

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
    var totalCourseDay by mutableIntStateOf(0)
    var totalCourseDays by mutableIntStateOf(0)
    var listUrlDiscoveryRunning by mutableStateOf(false)
    var listUrlDiscoveryMessage by mutableStateOf("")
    var listUrlChoiceVisible by mutableStateOf(false)
    var listUrlCandidates by mutableStateOf<List<AutoEwtListUrlCandidate>>(emptyList())

    fun showListUrlCandidates(candidates: List<AutoEwtListUrlCandidate>) {
        listUrlCandidates = candidates
        listUrlChoiceVisible = candidates.isNotEmpty()
    }

    fun clearListUrlCandidates() {
        listUrlChoiceVisible = false
        listUrlCandidates = emptyList()
    }

    fun updateListUrlDiscovery(running: Boolean, message: String) {
        listUrlDiscoveryRunning = running
        listUrlDiscoveryMessage = message
        if (!running) {
            clearListUrlCandidates()
        }
    }

    fun updateCourseDayProgress(day: Int, totalDays: Int) {
        totalCourseDays = totalDays.coerceAtLeast(0)
        totalCourseDay = day.coerceIn(0, totalCourseDays)
    }

    fun clearCourseDayProgress() {
        totalCourseDay = 0
        totalCourseDays = 0
    }

    fun clearAutomationProgress() {
        clearCourseDayProgress()
        loadProgress = 0
        videoStatus = ""
    }
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
    fun discoverListUrlFromUi()
    fun startAutomationFromUi()
    fun stopAutomationFromUi()
    fun requestFillLoginFromUi()
    fun requestProbeFromUi()
    fun restartSessionFromUi()
    fun saveConfigFromUi()
    fun saveAndOpenFromUi()
    fun selectListUrlCandidateFromUi(candidateId: String, title: String)
    fun cancelListUrlDiscoveryFromUi()
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
    var helpVisible by remember { mutableStateOf(false) }
    Surface(
        modifier = Modifier.fillMaxSize(),
        color = MaterialTheme.colorScheme.background
    ) {
        Box(modifier = Modifier.fillMaxSize()) {
            Column(modifier = Modifier.fillMaxSize()) {
                AppHeader(controller, onHelpClick = { helpVisible = true })
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
            if (state.listUrlChoiceVisible) {
                ListUrlCandidateDialog(controller)
            }
            if (helpVisible) {
                IconHelpDialog(onDismiss = { helpVisible = false })
            }
        }
    }
}

@Composable
private fun AppHeader(controller: AutoEwtUiController, onHelpClick: () -> Unit) {
    val state = controller.state
    val subtitle = when {
        state.listUrlDiscoveryRunning -> "正在获取课程列表 URL"
        state.automationRunning -> "自动化正在运行"
        else -> state.status
    }
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
                text = subtitle,
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
        IconButton(onClick = onHelpClick) {
            ActionIcon(R.drawable.ic_help, contentDescription = "帮助", size = 20.dp)
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun BrowserScreen(controller: AutoEwtUiController) {
    BoxWithConstraints(modifier = Modifier.fillMaxSize()) {
        val wide = maxWidth >= 600.dp
        val availableHeight = maxHeight
        val state = controller.state
        Box(modifier = Modifier.fillMaxSize()) {
            if (wide) {
                if (state.browserVisible) {
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
                    BrowserControlColumn(
                        controller = controller,
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(12.dp)
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
                        modifier = if (state.browserVisible) {
                            Modifier
                                .fillMaxWidth()
                                .heightIn(max = availableHeight * (if (state.automationRunning) 0.64f else 0.58f))
                        } else {
                            Modifier.fillMaxSize()
                        }
                    )
                    if (state.browserVisible) {
                        BrowserWorkspace(
                            controller = controller,
                            modifier = Modifier.weight(1f)
                        )
                    }
                }
            }
            if (!state.browserVisible) {
                HiddenBrowserHost(
                    controller = controller,
                    modifier = Modifier.align(Alignment.BottomEnd)
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
    var addressExpanded by remember { mutableStateOf(false) }
    val scrollModifier = if (compact) {
        Modifier.verticalScroll(rememberScrollState())
    } else {
        Modifier
    }
    Column(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surface)
            .then(scrollModifier)
            .padding(10.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        if (addressExpanded) {
            AddressBar(
                controller = controller,
                onCollapse = { addressExpanded = false }
            )
        }

        BrowserActionButtons(
            controller = controller,
            state = state,
            addressExpanded = addressExpanded,
            onToggleAddress = { addressExpanded = !addressExpanded }
        )

        if (compact) {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "视图与日志",
                        modifier = Modifier.weight(1f),
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                BrowserViewAndLogChips(controller, state)
            }
        } else {
            BrowserViewAndLogChips(controller, state)
        }

        RunStatusPanel(state)

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
private fun AddressBar(controller: AutoEwtUiController, onCollapse: (() -> Unit)? = null) {
    val state = controller.state
    BoxWithConstraints(modifier = Modifier.fillMaxWidth()) {
        val useStackedControls = maxWidth < 360.dp && onCollapse != null
        if (useStackedControls) {
            Column(verticalArrangement = Arrangement.spacedBy(DashboardButtonGap)) {
                OutlinedTextField(
                    value = state.url,
                    onValueChange = { state.url = it },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    label = { Text("地址") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri)
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(DashboardButtonGap)
                ) {
                    ActionButton(
                        icon = R.drawable.ic_open,
                        text = "打开",
                        onClick = { controller.openUrlFromUi(state.url) },
                        modifier = Modifier.weight(1f)
                    )
                    ActionButton(
                        icon = R.drawable.ic_arrow_up,
                        text = "收起",
                        onClick = onCollapse,
                        modifier = Modifier.weight(1f)
                    )
                }
            }
        } else {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(DashboardButtonGap)
            ) {
                OutlinedTextField(
                    value = state.url,
                    onValueChange = { state.url = it },
                    modifier = Modifier.weight(1f),
                    singleLine = true,
                    label = { Text("地址") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri)
                )
                ActionButton(
                    icon = R.drawable.ic_open,
                    text = "打开",
                    onClick = { controller.openUrlFromUi(state.url) },
                    modifier = Modifier.widthIn(min = 72.dp)
                )
                if (onCollapse != null) {
                    ActionButton(
                        icon = R.drawable.ic_arrow_up,
                        text = "收起",
                        onClick = onCollapse,
                        modifier = Modifier.widthIn(min = 72.dp)
                    )
                }
            }
        }
    }
}

@Composable
private fun RunStatusPanel(state: AutoEwtUiState) {
    val totalDays = state.totalCourseDays
    val hasDayProgress = totalDays > 0
    val hasVideoProgress = state.videoStatus.isNotBlank()
    val hasLoadProgress = state.loadProgress in 1..99
    if (!hasDayProgress && !hasVideoProgress && !hasLoadProgress) {
        return
    }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .padding(horizontal = 10.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Text(
                text = "运行状态",
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface
            )
            Spacer(Modifier.weight(1f))
            Text(
                text = if (state.automationRunning) "运行中" else "待机",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
        if (hasDayProgress) {
            val day = state.totalCourseDay.coerceIn(1, totalDays)
            ProgressMetric(
                label = "总刷课进度",
                value = "第 $day / $totalDays 天",
                progress = day.toFloat() / totalDays.toFloat()
            )
        }
        if (hasVideoProgress) {
            ProgressMetric(
                label = "当前视频",
                value = state.videoStatus.removePrefix("视频进度："),
                progress = state.loadProgress.coerceIn(0, 100) / 100f
            )
        } else if (hasLoadProgress) {
            ProgressMetric(
                label = "页面加载",
                value = "${state.loadProgress.coerceIn(0, 100)}%",
                progress = state.loadProgress.coerceIn(0, 100) / 100f
            )
        }
    }
}

@Composable
private fun ProgressMetric(label: String, value: String, progress: Float) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Text(
                text = label,
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
            Text(
                text = value,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.primary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
        UnifiedProgressBar(progress = progress, modifier = Modifier.fillMaxWidth())
    }
}

@Composable
private fun UnifiedProgressBar(progress: Float, modifier: Modifier = Modifier) {
    val clampedProgress = progress.coerceIn(0f, 1f)
    val shape = RoundedCornerShape(999.dp)
    Box(
        modifier = modifier
            .height(8.dp)
            .clip(shape)
            .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.16f))
    ) {
        if (clampedProgress > 0f) {
            Box(
                modifier = Modifier
                    .fillMaxHeight()
                    .fillMaxWidth(clampedProgress)
                    .clip(shape)
                    .background(MaterialTheme.colorScheme.primary)
            )
        }
    }
}

@Composable
private fun BrowserActionButtons(
    controller: AutoEwtUiController,
    state: AutoEwtUiState,
    addressExpanded: Boolean,
    onToggleAddress: () -> Unit
) {
    val actions = mutableListOf<ControlButtonSpec>()
    actions.add(
        ControlButtonSpec(
            icon = R.drawable.ic_link,
            text = "地址",
            selected = addressExpanded,
            onClick = onToggleAddress
        )
    )
    if (!state.automationRunning) {
        actions.add(ControlButtonSpec(R.drawable.ic_course, "打开课程", onClick = controller::openConfiguredCourseFromUi))
    }
    actions.add(
        ControlButtonSpec(
            icon = if (state.automationRunning) R.drawable.ic_stop else R.drawable.ic_play,
            text = if (state.automationRunning) "停止" else "开始刷课",
            primary = !state.automationRunning,
            destructive = state.automationRunning,
            onClick = if (state.automationRunning) controller::stopAutomationFromUi else controller::startAutomationFromUi
        )
    )
    actions.add(ControlButtonSpec(R.drawable.ic_restart, "重启", onClick = controller::restartSessionFromUi))
    if (!state.automationRunning) {
        actions.add(ControlButtonSpec(R.drawable.ic_login, "填登录", onClick = controller::requestFillLoginFromUi))
        actions.add(ControlButtonSpec(R.drawable.ic_probe, "探测", onClick = controller::requestProbeFromUi))
    }
    ControlButtonGrid(actions = actions, minCellWidth = DashboardButtonMinWidth)
}

@Composable
private fun BrowserViewAndLogChips(controller: AutoEwtUiController, state: AutoEwtUiState) {
    ControlButtonGrid(
        actions = listOf(
            ControlButtonSpec(
                icon = R.drawable.ic_open,
                text = "浏览器",
                selected = state.browserVisible,
                onClick = { controller.setBrowserVisibleFromUi(!state.browserVisible) }
            ),
            ControlButtonSpec(
                icon = R.drawable.ic_log_full,
                text = "完整",
                selected = state.logMode == AutoEwtUiState.LOG_MODE_FULL,
                onClick = { setLogMode(controller, AutoEwtUiState.LOG_MODE_FULL) }
            ),
            ControlButtonSpec(
                icon = R.drawable.ic_log_single,
                text = "单行",
                selected = state.logMode == AutoEwtUiState.LOG_MODE_SINGLE,
                onClick = { setLogMode(controller, AutoEwtUiState.LOG_MODE_SINGLE) }
            ),
            ControlButtonSpec(
                icon = R.drawable.ic_log_hidden,
                text = "隐藏",
                selected = state.logMode == AutoEwtUiState.LOG_MODE_HIDDEN,
                onClick = { setLogMode(controller, AutoEwtUiState.LOG_MODE_HIDDEN) }
            )
        ),
        minCellWidth = DashboardButtonMinWidth
    )
}

@Composable
private fun ControlButtonGrid(actions: List<ControlButtonSpec>, minCellWidth: Dp) {
    if (actions.isEmpty()) {
        return
    }
    BoxWithConstraints(modifier = Modifier.fillMaxWidth()) {
        val iconToolbarWidth = actions.size * DashboardIconButtonSize.value
        val useIconToolbar = maxWidth < 300.dp && iconToolbarWidth <= maxWidth.value
        if (useIconToolbar) {
            ControlIconToolbar(actions)
            return@BoxWithConstraints
        }
        val columns = responsiveColumnCount(actions.size, maxWidth, minCellWidth)
        Column(verticalArrangement = Arrangement.spacedBy(DashboardButtonGap)) {
            actions.chunked(columns).forEach { rowActions ->
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(DashboardButtonGap)
                ) {
                    rowActions.forEach { action ->
                        ActionButton(
                            icon = action.icon,
                            text = action.text,
                            enabled = action.enabled,
                            primary = action.primary,
                            destructive = action.destructive,
                            selected = action.selected,
                            onClick = action.onClick,
                            modifier = Modifier.weight(1f)
                        )
                    }
                    repeat(columns - rowActions.size) {
                        Spacer(modifier = Modifier.weight(1f))
                    }
                }
            }
        }
    }
}

@Composable
private fun ControlIconToolbar(actions: List<ControlButtonSpec>) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically
    ) {
        actions.forEach { action ->
            Box(
                modifier = Modifier.weight(1f),
                contentAlignment = Alignment.Center
            ) {
                IconActionButton(action)
            }
        }
    }
}

private fun responsiveColumnCount(itemCount: Int, maxWidth: Dp, minCellWidth: Dp): Int {
    if (itemCount <= 1) {
        return 1
    }
    val fitCount = ((maxWidth.value + DashboardButtonGap.value) / (minCellWidth.value + DashboardButtonGap.value))
        .toInt()
        .coerceIn(1, itemCount)
    if (itemCount == 4 && fitCount == 3) {
        return 2
    }
    return fitCount
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
            Box(modifier = Modifier.fillMaxSize()) {
                BrowserView(controller, Modifier.fillMaxSize())
                if (state.listUrlDiscoveryRunning) {
                    ListUrlDiscoveryBanner(
                        message = state.listUrlDiscoveryMessage.ifBlank { "正在获取课程列表 URL" },
                        modifier = Modifier
                            .align(Alignment.TopCenter)
                            .padding(10.dp)
                    )
                }
            }
        } else {
            HiddenBrowserHost(controller)
        }
    }
}

@Composable
private fun HiddenBrowserHost(controller: AutoEwtUiController, modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .size(1.dp)
            .clip(RoundedCornerShape(1.dp))
    ) {
        BrowserView(controller, Modifier.size(1.dp))
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
private fun ListUrlDiscoveryBanner(message: String, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        color = MaterialTheme.colorScheme.primaryContainer,
        contentColor = MaterialTheme.colorScheme.onPrimaryContainer,
        tonalElevation = 4.dp,
        shadowElevation = 2.dp
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            ActionIcon(R.drawable.ic_probe)
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "正在获取课程列表 URL",
                    style = MaterialTheme.typography.labelLarge,
                    fontWeight = FontWeight.SemiBold
                )
                Text(
                    text = message,
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis
                )
            }
        }
    }
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
                        .padding(start = 10.dp, top = 10.dp, end = 10.dp, bottom = 62.dp)
                ) {
                    Text(
                        text = state.fullLog.ifBlank { "暂无日志" },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                AnimatedVisibility(
                    visible = !atBottom,
                    modifier = Modifier
                        .align(Alignment.BottomEnd)
                        .padding(horizontal = 6.dp, vertical = 6.dp),
                    enter = fadeIn() + slideInVertically(initialOffsetY = { it / 2 }),
                    exit = fadeOut() + slideOutVertically(targetOffsetY = { it / 2 })
                ) {
                    Surface(
                        shape = RoundedCornerShape(22.dp),
                        color = MaterialTheme.colorScheme.surface,
                        contentColor = MaterialTheme.colorScheme.primary,
                        tonalElevation = 3.dp,
                        shadowElevation = 2.dp
                    ) {
                        IconButton(
                            onClick = {
                                followTail = true
                                scope.launch {
                                    scroll.animateScrollTo(scroll.maxValue)
                                }
                            },
                            modifier = Modifier.size(42.dp)
                        ) {
                            Icon(
                                painter = painterResource(R.drawable.ic_arrow_down),
                                contentDescription = "跳到最新日志",
                                modifier = Modifier.size(20.dp)
                            )
                        }
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
private fun ListUrlCandidateDialog(controller: AutoEwtUiController) {
    val candidates = controller.state.listUrlCandidates
    AlertDialog(
        onDismissRequest = controller::cancelListUrlDiscoveryFromUi,
        shape = RoundedCornerShape(8.dp),
        containerColor = MaterialTheme.colorScheme.surface,
        titleContentColor = MaterialTheme.colorScheme.onSurface,
        textContentColor = MaterialTheme.colorScheme.onSurface,
        title = {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    text = "选择课程列表 URL",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold
                )
                Text(
                    text = "找到 ${candidates.size} 个未完成任务",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        },
        text = {
            LazyColumn(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(max = 420.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                items(candidates, key = { it.id }) { candidate ->
                    ListUrlCandidateRow(
                        candidate = candidate,
                        onClick = {
                            controller.selectListUrlCandidateFromUi(candidate.id, candidate.title)
                        }
                    )
                }
            }
        },
        confirmButton = {},
        dismissButton = {
            TextButton(onClick = controller::cancelListUrlDiscoveryFromUi) {
                Text("取消")
            }
        }
    )
}

@Composable
private fun IconHelpDialog(onDismiss: () -> Unit) {
    val mainActions = listOf(
        HelpIconSpec(R.drawable.ic_link, "地址", "展开课程列表 URL 输入框；日常运行时默认隐藏，减少浏览器页占用。"),
        HelpIconSpec(R.drawable.ic_course, "打开课程", "按配置里的课程列表 URL 进入任务页。已完成任务会自动隐藏，已截止但未完成的任务仍可进入。"),
        HelpIconSpec(R.drawable.ic_play, "开始刷课", "按配置页的模式运行：刷课会处理视频、FM 与检查点；做题会按 report_id/任务入口执行。"),
        HelpIconSpec(R.drawable.ic_stop, "停止", "停止当前自动化任务；正常停止不会触发崩溃重启。"),
        HelpIconSpec(R.drawable.ic_restart, "重启", "重建 GeckoView 浏览器会话。视频卡死、页面不再播放或兼容异常时可以使用。"),
        HelpIconSpec(R.drawable.ic_login, "填登录", "把配置页账号密码填入页面。验证码、短信验证仍需要手动完成。"),
        HelpIconSpec(R.drawable.ic_probe, "探测", "手动探测当前页面课程/日期/按钮状态；自动探测会按间隔重试，手动探测才输出页面摘要。")
    )
    val viewActions = listOf(
        HelpIconSpec(R.drawable.ic_open, "浏览器", "显示或隐藏内嵌浏览器。隐藏时浏览器仍挂载运行，进度和自动化不会暂停。"),
        HelpIconSpec(R.drawable.ic_log_full, "完整日志", "显示完整运行日志；向下箭头只在未滑到底部时出现。"),
        HelpIconSpec(R.drawable.ic_log_single, "单行日志", "只保留最新一行，适合窄屏观察进度。"),
        HelpIconSpec(R.drawable.ic_log_hidden, "隐藏日志", "完全收起日志，把空间留给浏览器和进度。")
    )
    AlertDialog(
        onDismissRequest = onDismiss,
        shape = RoundedCornerShape(8.dp),
        containerColor = MaterialTheme.colorScheme.surface,
        title = {
            Text(
                text = "使用帮助",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold
            )
        },
        text = {
            LazyColumn(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(max = 520.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                item {
                    HelpSectionTitle("基本流程")
                    HelpParagraph("先到配置页填写账号、密码和课程列表 URL；也可以使用“自动获取课程列表 URL”登录后选择具体任务。")
                    HelpParagraph("高级设置里可切换刷课/做题、起始天数、自动登录、桌面浏览器模式等。默认桌面浏览器模式更接近电脑端页面。")
                }
                item {
                    HelpSectionTitle("浏览器页图标")
                }
                items(mainActions, key = { it.title }) { item ->
                    HelpIconRow(item)
                }
                item {
                    HelpSectionTitle("视图与日志")
                }
                items(viewActions, key = { it.title }) { item ->
                    HelpIconRow(item)
                }
                item {
                    HelpSectionTitle("自动化边界")
                    HelpParagraph("Android 版内置 GeckoView，不需要安装浏览器驱动；当前主线最低支持 Android 8.0。")
                    HelpParagraph("软件会实时处理视频检查点、暂停提示和“错过所有看课检测点”的重刷入口，但不会绕过验证码或短信验证。")
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) {
                Text("知道了")
            }
        }
    )
}

@Composable
private fun HelpSectionTitle(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.labelLarge,
        fontWeight = FontWeight.SemiBold,
        color = MaterialTheme.colorScheme.onSurface
    )
}

@Composable
private fun HelpParagraph(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant
    )
}

@Composable
private fun HelpIconRow(item: HelpIconSpec) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.Top,
        horizontalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Surface(
            modifier = Modifier.size(32.dp),
            shape = RoundedCornerShape(8.dp),
            color = MaterialTheme.colorScheme.surfaceVariant,
            contentColor = MaterialTheme.colorScheme.primary
        ) {
            Box(contentAlignment = Alignment.Center) {
                ActionIcon(item.icon, contentDescription = item.title, size = 17.dp)
            }
        }
        Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(
                text = item.title,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface
            )
            Text(
                text = item.body,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}

@Composable
private fun ListUrlCandidateRow(candidate: AutoEwtListUrlCandidate, onClick: () -> Unit) {
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .clickable(onClick = onClick),
        shape = RoundedCornerShape(8.dp),
        color = MaterialTheme.colorScheme.surfaceVariant,
        contentColor = MaterialTheme.colorScheme.onSurfaceVariant
    ) {
        Column(
            modifier = Modifier.padding(10.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp)
        ) {
            Text(
                text = candidate.title.ifBlank { "未命名任务" },
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = candidate.status.ifBlank { "未完成" },
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                if (candidate.filter.isNotBlank() && !candidate.status.contains(candidate.filter)) {
                    Text(
                        text = candidate.filter,
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.tertiary,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }
            if (candidate.deadline.isNotBlank()) {
                CandidateMetaLine("截止", candidate.deadline)
            }
            if (candidate.startTime.isNotBlank()) {
                CandidateMetaLine("开始", candidate.startTime)
            }
            if (candidate.teacher.isNotBlank()) {
                CandidateMetaLine("布置人", candidate.teacher)
            }
        }
    }
}

@Composable
private fun CandidateMetaLine(label: String, value: String) {
    Text(
        text = "$label：$value",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis
    )
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
        FlowRow(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Button(onClick = controller::saveConfigFromUi) {
                Text("保存配置")
            }
            OutlinedButton(onClick = controller::saveAndOpenFromUi) {
                Text("保存并打开")
            }
        }
        Button(
            onClick = controller::discoverListUrlFromUi,
            modifier = Modifier.fillMaxWidth(),
            contentPadding = ButtonDefaults.ButtonWithIconContentPadding
        ) {
            ActionIcon(R.drawable.ic_probe)
            Spacer(Modifier.width(6.dp))
            Text("自动获取课程列表 URL")
        }
    }
}

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
private fun ConfigRunPanel(controller: AutoEwtUiController, modifier: Modifier = Modifier) {
    val state = controller.state
    var advancedExpanded by remember { mutableStateOf(false) }
    Panel(modifier = modifier) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Column(modifier = Modifier.weight(1f)) {
                SectionTitle("高级设置")
                Text(
                    text = "当前：${if (state.mode == "paper") "做题" else "刷课"}，从第 ${state.dayToStartOn.ifBlank { "1" }} 天开始，${if (state.desktopMode) "桌面浏览器模式" else "移动浏览器模式"}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis
                )
            }
            OutlinedButton(onClick = { advancedExpanded = !advancedExpanded }) {
                Text(if (advancedExpanded) "收起" else "展开")
            }
        }
        if (advancedExpanded) {
            WarningBox("高级设置会改变自动化入口、登录行为和浏览器 UA/viewport。改错可能导致无法登录、日期/课程识别异常或触发浏览器重启；除非排查兼容问题，建议保持默认。")
            SectionTitle("任务设置")
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
            SectionTitle("登录与浏览器兼容")
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
}

@Composable
private fun WarningBox(text: String) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        color = MaterialTheme.colorScheme.errorContainer,
        contentColor = MaterialTheme.colorScheme.onErrorContainer
    ) {
        Text(
            text = text,
            modifier = Modifier.padding(10.dp),
            style = MaterialTheme.typography.bodySmall
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
    selected: Boolean = false,
    modifier: Modifier = Modifier,
    onClick: () -> Unit
) {
    Surface(
        modifier = modifier.height(DashboardButtonHeight),
        shape = DashboardButtonShape,
        color = Color.Transparent
    ) {
        val content: @Composable () -> Unit = {
            ActionIcon(icon)
            Spacer(Modifier.width(4.dp))
            Text(
                text = text,
                style = MaterialTheme.typography.labelMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
        CompositionLocalProvider(LocalMinimumInteractiveComponentSize provides 0.dp) {
            when {
                destructive -> OutlinedButton(
                    onClick = onClick,
                    enabled = enabled,
                    contentPadding = DashboardButtonPadding,
                    colors = ButtonDefaults.outlinedButtonColors(
                        contentColor = MaterialTheme.colorScheme.error
                    ),
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.error.copy(alpha = 0.5f)),
                    shape = DashboardButtonShape,
                    modifier = Modifier.fillMaxSize()
                ) {
                    content()
                }
                primary -> Button(
                    onClick = onClick,
                    enabled = enabled,
                    contentPadding = DashboardButtonPadding,
                    shape = DashboardButtonShape,
                    modifier = Modifier.fillMaxSize()
                ) {
                    content()
                }
                selected -> FilledTonalButton(
                    onClick = onClick,
                    enabled = enabled,
                    contentPadding = DashboardButtonPadding,
                    colors = ButtonDefaults.filledTonalButtonColors(
                        containerColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.12f),
                        contentColor = MaterialTheme.colorScheme.primary
                    ),
                    shape = DashboardButtonShape,
                    modifier = Modifier.fillMaxSize()
                ) {
                    content()
                }
                else -> OutlinedButton(
                    onClick = onClick,
                    enabled = enabled,
                    contentPadding = DashboardButtonPadding,
                    colors = ButtonDefaults.outlinedButtonColors(
                        contentColor = MaterialTheme.colorScheme.onSurfaceVariant
                    ),
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
                    shape = DashboardButtonShape,
                    modifier = Modifier.fillMaxSize()
                ) {
                    content()
                }
            }
        }
    }
}

@Composable
private fun IconActionButton(action: ControlButtonSpec, modifier: Modifier = Modifier) {
    CompositionLocalProvider(LocalMinimumInteractiveComponentSize provides 0.dp) {
        when {
            action.destructive -> OutlinedIconButton(
                onClick = action.onClick,
                enabled = action.enabled,
                colors = IconButtonDefaults.outlinedIconButtonColors(
                    contentColor = MaterialTheme.colorScheme.error
                ),
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.error.copy(alpha = 0.5f)),
                modifier = modifier.size(DashboardIconButtonSize)
            ) {
                ActionIcon(action.icon, contentDescription = action.text, size = 17.dp)
            }
            action.primary -> FilledIconButton(
                onClick = action.onClick,
                enabled = action.enabled,
                modifier = modifier.size(DashboardIconButtonSize)
            ) {
                ActionIcon(action.icon, contentDescription = action.text, size = 17.dp)
            }
            action.selected -> FilledTonalIconButton(
                onClick = action.onClick,
                enabled = action.enabled,
                colors = IconButtonDefaults.filledTonalIconButtonColors(
                    containerColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.12f),
                    contentColor = MaterialTheme.colorScheme.primary
                ),
                modifier = modifier.size(DashboardIconButtonSize)
            ) {
                ActionIcon(action.icon, contentDescription = action.text, size = 17.dp)
            }
            else -> OutlinedIconButton(
                onClick = action.onClick,
                enabled = action.enabled,
                colors = IconButtonDefaults.outlinedIconButtonColors(
                    contentColor = MaterialTheme.colorScheme.onSurfaceVariant
                ),
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
                modifier = modifier.size(DashboardIconButtonSize)
            ) {
                ActionIcon(action.icon, contentDescription = action.text, size = 17.dp)
            }
        }
    }
}

@Composable
private fun ActionIcon(icon: Int, contentDescription: String? = null, size: Dp = 16.dp) {
    Icon(
        painter = painterResource(icon),
        contentDescription = contentDescription,
        modifier = Modifier.size(size)
    )
}

private fun setLogMode(controller: AutoEwtUiController, mode: Int) {
    while (controller.state.logMode != mode) {
        controller.cycleLogModeFromUi()
    }
}
