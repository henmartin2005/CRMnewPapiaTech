package com.papiatech.crm

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.text.NumberFormat
import java.util.Locale

private const val BASE_URL = "https://datos.papiatech.com/api/mobile"

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        createNotificationChannel()
        requestNotificationPermission()
        setContent {
            PapiaTheme {
                PapiaApp(applicationContext)
            }
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = getSystemService(NotificationManager::class.java)
            val channel = NotificationChannel(
                WHATSAPP_CHANNEL_ID,
                "WhatsApp CRM",
                NotificationManager.IMPORTANCE_DEFAULT,
            ).apply {
                description = "Notificaciones de mensajes nuevos de WhatsApp"
            }
            manager.createNotificationChannel(channel)
        }
    }

    private fun requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1001)
        }
    }
}

data class DashboardStats(
    val totalLeads: Int = 0,
    val activeClients: Int = 0,
    val pendingProposals: Int = 0,
    val totalBilled: Double = 0.0,
    val totalCollected: Double = 0.0,
    val totalPending: Double = 0.0,
)

data class Client(
    val id: Int,
    val firstName: String,
    val lastName: String,
    val email: String,
    val phone: String,
    val company: String,
    val projectType: String,
    val pipelineStage: String,
    val projectDetails: String,
    val totalCost: Double,
    val amountPaid: Double,
    val pending: Double,
) {
    val fullName: String get() = listOf(firstName, lastName).filter { it.isNotBlank() }.joinToString(" ")
}

data class TaskItem(
    val id: Int,
    val clientId: Int,
    val clientName: String,
    val summary: String,
    val comment: String,
    val nextAt: String,
    val nextDate: String,
    val completed: Boolean,
)

data class PipelineStage(val key: String, val label: String, val clients: List<Client> = emptyList())

data class WhatsAppConversation(
    val phone: String,
    val clientName: String,
    val lastMessage: String,
    val lastDirection: String,
    val lastAt: String,
    val unread: Int,
)

data class WhatsAppSummary(
    val unread: Int,
    val conversations: List<WhatsAppConversation>,
)

data class WhatsAppMessage(
    val id: Int,
    val direction: String,
    val message: String,
    val status: String,
    val createdAt: String,
) {
    val inbound: Boolean get() = direction == "inbound"
}

class PapiaApi(private val context: Context) {
    private val prefs = context.getSharedPreferences("papia_crm", Context.MODE_PRIVATE)
    var token: String?
        get() = prefs.getString("token", null)
        private set(value) = prefs.edit().putString("token", value).apply()

    fun signOut() {
        prefs.edit().remove("token").apply()
    }

    suspend fun login(username: String, password: String): Boolean = withContext(Dispatchers.IO) {
        val body = JSONObject().put("username", username).put("password", password)
        val json = request("POST", "/login", body, authorized = false)
        val ok = json.optBoolean("ok")
        if (ok) token = json.optString("token")
        ok
    }

    suspend fun dashboard(): DashboardStats = withContext(Dispatchers.IO) {
        val stats = request("GET", "/dashboard").getJSONObject("stats")
        DashboardStats(
            totalLeads = stats.optInt("total_leads"),
            activeClients = stats.optInt("active_clients"),
            pendingProposals = stats.optInt("pending_proposals"),
            totalBilled = stats.optDouble("total_billed"),
            totalCollected = stats.optDouble("total_collected"),
            totalPending = stats.optDouble("total_pending"),
        )
    }

    suspend fun clients(): List<Client> = withContext(Dispatchers.IO) {
        request("GET", "/clients").getJSONArray("clients").mapObjects { it.toClient() }
    }

    suspend fun pipeline(): List<PipelineStage> = withContext(Dispatchers.IO) {
        request("GET", "/pipeline").getJSONArray("stages").mapObjects { item ->
            PipelineStage(
                key = item.optString("key"),
                label = item.optString("label"),
                clients = item.optJSONArray("clients")?.mapObjects { it.toClient() } ?: emptyList(),
            )
        }
    }

    suspend fun tasks(): List<TaskItem> = withContext(Dispatchers.IO) {
        request("GET", "/tasks").getJSONArray("tasks").mapObjects { item ->
            TaskItem(
                id = item.optInt("id"),
                clientId = item.optInt("client_id"),
                clientName = item.optString("client_name"),
                summary = item.optString("summary"),
                comment = item.optString("reminder_comment"),
                nextAt = item.optString("next_at"),
                nextDate = item.optString("next_date"),
                completed = item.optBoolean("completed"),
            )
        }
    }

    suspend fun whatsapp(): WhatsAppSummary = withContext(Dispatchers.IO) {
        val json = request("GET", "/whatsapp")
        WhatsAppSummary(
            unread = json.optInt("unread"),
            conversations = json.getJSONArray("conversations").mapObjects { item ->
                WhatsAppConversation(
                    phone = item.optString("phone"),
                    clientName = item.optString("client_name"),
                    lastMessage = item.optString("last_message"),
                    lastDirection = item.optString("last_direction"),
                    lastAt = item.optString("last_at"),
                    unread = item.optInt("unread"),
                )
            },
        )
    }

    suspend fun whatsappMessages(phone: String, markRead: Boolean = true): List<WhatsAppMessage> = withContext(Dispatchers.IO) {
        val encoded = URLEncoder.encode(phone, "UTF-8")
        val mark = if (markRead) "1" else "0"
        request("GET", "/whatsapp/messages?phone=$encoded&mark_read=$mark")
            .getJSONArray("messages")
            .mapObjects { item ->
                WhatsAppMessage(
                    id = item.optInt("id"),
                    direction = item.optString("direction"),
                    message = item.optString("message"),
                    status = item.optString("status"),
                    createdAt = item.optString("created_at"),
                )
            }
    }

    private fun request(
        method: String,
        path: String,
        body: JSONObject? = null,
        authorized: Boolean = true,
    ): JSONObject {
        val connection = (URL(BASE_URL + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 15_000
            readTimeout = 20_000
            setRequestProperty("Accept", "application/json")
            if (authorized) setRequestProperty("Authorization", "Bearer ${token.orEmpty()}")
            if (body != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
                OutputStreamWriter(outputStream).use { it.write(body.toString()) }
            }
        }
        val stream = if (connection.responseCode in 200..299) connection.inputStream else connection.errorStream
        val text = stream.bufferedReader().use(BufferedReader::readText)
        val json = JSONObject(text.ifBlank { "{}" })
        if (connection.responseCode !in 200..299 || !json.optBoolean("ok", true)) {
            throw IllegalStateException(json.optString("error", "No se pudo conectar al CRM."))
        }
        return json
    }
}

private fun JSONObject.toClient() = Client(
    id = optInt("id"),
    firstName = optString("first_name"),
    lastName = optString("last_name"),
    email = optString("email"),
    phone = optString("phone"),
    company = optString("company"),
    projectType = optString("project_type"),
    pipelineStage = optString("pipeline_stage"),
    projectDetails = optString("project_details"),
    totalCost = optDouble("total_cost"),
    amountPaid = optDouble("amount_paid"),
    pending = optDouble("pending"),
)

private inline fun <T> JSONArray.mapObjects(block: (JSONObject) -> T): List<T> =
    List(length()) { index -> block(getJSONObject(index)) }

@Composable
fun PapiaApp(context: Context) {
    val api = remember { PapiaApi(context) }
    var signedIn by remember { mutableStateOf(api.token != null) }

    if (!signedIn) {
        LoginScreen(api = api, onSignedIn = { signedIn = true })
    } else {
        MainScreen(api = api, onSignOut = {
            api.signOut()
            signedIn = false
        })
    }
}

@Composable
fun LoginScreen(api: PapiaApi, onSignedIn: () -> Unit) {
    val scope = rememberCoroutineScope()
    var username by remember { mutableStateOf("admin") }
    var password by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    Surface(Modifier.fillMaxSize(), color = PapiaColors.Background) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(24.dp),
            verticalArrangement = Arrangement.Center,
        ) {
            Text("Papia CRM", style = MaterialTheme.typography.headlineLarge, fontWeight = FontWeight.Bold)
            Text("Datos, clientes y tareas en tu Android", color = PapiaColors.Muted)
            Spacer(Modifier.height(28.dp))
            OutlinedTextField(
                value = username,
                onValueChange = { username = it },
                label = { Text("Usuario") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                label = { Text("Contraseña") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                modifier = Modifier.fillMaxWidth(),
            )
            error?.let {
                Spacer(Modifier.height(10.dp))
                Text(it, color = PapiaColors.Danger)
            }
            Spacer(Modifier.height(18.dp))
            Button(
                onClick = {
                    loading = true
                    error = null
                    scope.launch {
                        runCatching { api.login(username.trim(), password) }
                            .onSuccess { if (it) onSignedIn() else error = "Credenciales incorrectas." }
                            .onFailure { error = it.message ?: "No se pudo iniciar sesión." }
                        loading = false
                    }
                },
                enabled = !loading && username.isNotBlank() && password.isNotBlank(),
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(8.dp),
            ) {
                if (loading) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                else Text("Entrar")
            }
        }
    }
}

enum class Tab(val label: String) {
    Dashboard("Inicio"),
    Clients("Clientes"),
    Pipeline("Pipeline"),
    WhatsApp("WhatsApp"),
    Tasks("Tasks"),
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(api: PapiaApi, onSignOut: () -> Unit) {
    var selected by remember { mutableStateOf(Tab.Dashboard) }
    var whatsappSummary by remember { mutableStateOf<WhatsAppSummary?>(null) }
    WhatsAppNotificationMonitor(
        api = api,
        onSummary = { whatsappSummary = it },
    )
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Papia CRM", fontWeight = FontWeight.SemiBold) },
                actions = {
                    NotificationPill(count = whatsappSummary?.unread ?: 0)
                    TextButton(onClick = onSignOut) { Text("Salir") }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = PapiaColors.Background),
            )
        },
        bottomBar = {
            NavigationBar(containerColor = Color.White) {
                Tab.entries.forEach { tab ->
                    NavigationBarItem(
                        selected = selected == tab,
                        onClick = { selected = tab },
                        icon = { Text(tab.label.first().toString()) },
                        label = { Text(tab.label) },
                    )
                }
            }
        },
        containerColor = PapiaColors.Background,
    ) { padding ->
        Box(Modifier.padding(padding)) {
            when (selected) {
                Tab.Dashboard -> DashboardScreen(api, whatsappSummary)
                Tab.Clients -> ClientsScreen(api)
                Tab.Pipeline -> PipelineScreen(api)
                Tab.WhatsApp -> WhatsAppScreen(
                    api = api,
                    initialSummary = whatsappSummary,
                    onSummary = { whatsappSummary = it },
                )
                Tab.Tasks -> TasksScreen(api)
            }
        }
    }
}

@Composable
fun WhatsAppNotificationMonitor(api: PapiaApi, onSummary: (WhatsAppSummary) -> Unit) {
    val context = LocalContext.current
    LaunchedEffect(api) {
        var lastUnread: Int? = null
        while (true) {
            runCatching { api.whatsapp() }
                .onSuccess { summary ->
                    onSummary(summary)
                    val unread = summary.unread
                    val previous = lastUnread
                    if (previous != null && unread > previous) {
                        val latestUnread = summary.conversations.firstOrNull { it.unread > 0 }
                        showWhatsAppNotification(context, latestUnread, unread - previous, unread)
                    }
                    lastUnread = unread
                }
            delay(30_000)
        }
    }
}

@Composable
fun DashboardScreen(api: PapiaApi, whatsappSummary: WhatsAppSummary?) {
    var stats by remember { mutableStateOf<DashboardStats?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        runCatching { api.dashboard() }
            .onSuccess { stats = it }
            .onFailure { error = it.message }
    }
    ContentFrame(title = "Dashboard", error = error, loading = stats == null && error == null) {
        val data = stats ?: return@ContentFrame
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
            MetricCard("Leads", data.totalLeads.toString(), Modifier.weight(1f))
            MetricCard("Activos", data.activeClients.toString(), Modifier.weight(1f))
        }
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
            MetricCard("Propuestas", data.pendingProposals.toString(), Modifier.weight(1f))
            MetricCard("Pendiente", money(data.totalPending), Modifier.weight(1f), PapiaColors.Danger)
        }
        Spacer(Modifier.height(12.dp))
        InfoCard("Facturado", money(data.totalBilled))
        InfoCard("Cobrado", money(data.totalCollected), PapiaColors.Success)
        Spacer(Modifier.height(10.dp))
        SectionTitle("Últimos WhatsApp")
        val chats = whatsappSummary?.conversations.orEmpty().take(3)
        if (chats.isEmpty()) {
            EmptyState("No hay conversaciones recientes.")
        } else {
            chats.forEach { WhatsAppRow(it) }
        }
    }
}

@Composable
fun ClientsScreen(api: PapiaApi) {
    var clients by remember { mutableStateOf<List<Client>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var selected by remember { mutableStateOf<Client?>(null) }
    LaunchedEffect(Unit) {
        runCatching { api.clients() }
            .onSuccess { clients = it }
            .onFailure { error = it.message }
    }
    ContentFrame(title = "Clientes", error = error, loading = clients == null && error == null) {
        selected?.let { ClientDetail(it, onClose = { selected = null }) }
        clients?.forEach { client ->
            ClientRow(client = client, onClick = { selected = client })
        }
    }
}

@Composable
fun PipelineScreen(api: PapiaApi) {
    var stages by remember { mutableStateOf<List<PipelineStage>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        runCatching { api.pipeline() }
            .onSuccess { stages = it }
            .onFailure { error = it.message }
    }
    ContentFrame(title = "Pipeline", error = error, loading = stages == null && error == null) {
        stages?.forEach { stage ->
            SectionTitle("${stage.label} · ${stage.clients.size}")
            stage.clients.take(4).forEach { client -> ClientRow(client = client) }
            if (stage.clients.size > 4) {
                Text("+${stage.clients.size - 4} más", color = PapiaColors.Muted, modifier = Modifier.padding(8.dp))
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}

@Composable
fun TasksScreen(api: PapiaApi) {
    var tasks by remember { mutableStateOf<List<TaskItem>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        runCatching { api.tasks() }
            .onSuccess { tasks = it }
            .onFailure { error = it.message }
    }
    ContentFrame(title = "Tasks", error = error, loading = tasks == null && error == null) {
        tasks?.forEach { task ->
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(bottom = 8.dp),
                shape = RoundedCornerShape(8.dp),
                colors = CardDefaults.cardColors(containerColor = Color.White),
            ) {
                Column(Modifier.padding(14.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        StatusDot(if (task.completed) PapiaColors.Success else PapiaColors.Warning)
                        Spacer(Modifier.width(8.dp))
                        Text(task.clientName.ifBlank { "Cliente" }, fontWeight = FontWeight.SemiBold)
                    }
                    Text(task.summary, color = PapiaColors.Text)
                    Text(task.nextAt.ifBlank { task.nextDate }, color = PapiaColors.Muted, style = MaterialTheme.typography.bodySmall)
                    if (task.comment.isNotBlank()) Text(task.comment, color = PapiaColors.Muted, maxLines = 2)
                }
            }
        }
    }
}

@Composable
fun WhatsAppScreen(
    api: PapiaApi,
    initialSummary: WhatsAppSummary?,
    onSummary: (WhatsAppSummary) -> Unit,
) {
    var summary by remember { mutableStateOf(initialSummary) }
    var error by remember { mutableStateOf<String?>(null) }
    var query by remember { mutableStateOf("") }
    var selected by remember { mutableStateOf<WhatsAppConversation?>(null) }
    var messages by remember { mutableStateOf<List<WhatsAppMessage>?>(null) }
    val scope = rememberCoroutineScope()

    fun refresh() {
        scope.launch {
            runCatching { api.whatsapp() }
                .onSuccess {
                    summary = it
                    onSummary(it)
                }
                .onFailure { error = it.message }
        }
    }

    LaunchedEffect(Unit) {
        runCatching { api.whatsapp() }
            .onSuccess {
                summary = it
                onSummary(it)
            }
            .onFailure { error = it.message }
    }

    LaunchedEffect(selected?.phone) {
        val conversation = selected ?: return@LaunchedEffect
        messages = null
        runCatching { api.whatsappMessages(conversation.phone, markRead = true) }
            .onSuccess {
                messages = it
                refresh()
            }
            .onFailure { error = it.message }
    }

    ContentFrame(title = "WhatsApp", error = error, loading = summary == null && error == null) {
        val data = summary ?: return@ContentFrame
        selected?.let { conversation ->
            ChatDetail(
                conversation = conversation,
                messages = messages,
                onClose = {
                    selected = null
                    messages = null
                },
            )
            return@ContentFrame
        }
        InfoCard("Mensajes sin leer", data.unread.toString(), if (data.unread > 0) PapiaColors.Success else PapiaColors.Text)
        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            label = { Text("Buscar conversación") },
            singleLine = true,
            modifier = Modifier
                .fillMaxWidth()
                .padding(bottom = 10.dp),
        )
        Spacer(Modifier.height(8.dp))
        val filtered = data.conversations.filter {
            val text = "${it.clientName} ${it.phone} ${it.lastMessage}".lowercase()
            text.contains(query.trim().lowercase())
        }
        if (filtered.isEmpty()) {
            EmptyState("No hay conversaciones para mostrar.")
        } else {
            filtered.forEach { conversation ->
                WhatsAppRow(conversation = conversation, onClick = { selected = conversation })
            }
        }
    }
}

@Composable
fun ContentFrame(
    title: String,
    error: String?,
    loading: Boolean,
    content: @Composable ColumnScope.() -> Unit,
) {
    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(0.dp),
    ) {
        item {
            Text(title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(12.dp))
        }
        if (loading) {
            item {
                Box(Modifier.fillMaxWidth().padding(40.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
            }
        }
        if (error != null) {
            item { Text(error, color = PapiaColors.Danger) }
        }
        item { Column(modifier = Modifier.fillMaxWidth(), content = content) }
    }
}

@Composable
fun MetricCard(label: String, value: String, modifier: Modifier = Modifier, valueColor: Color = PapiaColors.Text) {
    Card(modifier = modifier, shape = RoundedCornerShape(8.dp), colors = CardDefaults.cardColors(Color.White)) {
        Column(Modifier.padding(14.dp)) {
            Text(label, color = PapiaColors.Muted, style = MaterialTheme.typography.bodySmall)
            Text(value, color = valueColor, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleLarge)
        }
    }
}

@Composable
fun InfoCard(label: String, value: String, valueColor: Color = PapiaColors.Text) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 8.dp),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(Color.White),
    ) {
        Row(Modifier.padding(14.dp), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(label, color = PapiaColors.Muted)
            Text(value, color = valueColor, fontWeight = FontWeight.SemiBold)
        }
    }
}

@Composable
fun ClientRow(client: Client, onClick: (() -> Unit)? = null) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 8.dp)
            .then(if (onClick != null) Modifier.clickable { onClick() } else Modifier),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White),
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Avatar(client.fullName.ifBlank { client.company })
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(client.fullName.ifBlank { "Sin nombre" }, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(client.company.ifBlank { client.email.ifBlank { client.phone } }, color = PapiaColors.Muted, maxLines = 1)
                Text(client.projectType.replace("_", " "), color = PapiaColors.Blue, style = MaterialTheme.typography.bodySmall)
            }
            Text(money(client.pending), color = if (client.pending > 0) PapiaColors.Danger else PapiaColors.Success)
        }
    }
}

@Composable
fun WhatsAppRow(conversation: WhatsAppConversation, onClick: (() -> Unit)? = null) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 8.dp)
            .then(if (onClick != null) Modifier.clickable { onClick() } else Modifier),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White),
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Avatar(conversation.clientName.ifBlank { conversation.phone })
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        conversation.clientName.ifBlank { conversation.phone },
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                    if (conversation.unread > 0) {
                        Text(
                            conversation.unread.toString(),
                            color = Color.White,
                            modifier = Modifier
                                .clip(CircleShape)
                                .background(PapiaColors.Success)
                                .padding(horizontal = 8.dp, vertical = 2.dp),
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }
                Text(conversation.lastMessage.ifBlank { "Sin mensaje" }, color = PapiaColors.Text, maxLines = 2)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(conversation.lastAt, color = PapiaColors.Muted, style = MaterialTheme.typography.bodySmall)
                    if (conversation.lastDirection == "inbound") {
                        Text("  ·  recibido", color = PapiaColors.Success, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
    }
}

@Composable
fun ChatDetail(
    conversation: WhatsAppConversation,
    messages: List<WhatsAppMessage>?,
    onClose: () -> Unit,
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 12.dp),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White),
    ) {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(
                        conversation.clientName.ifBlank { conversation.phone },
                        fontWeight = FontWeight.Bold,
                        style = MaterialTheme.typography.titleMedium,
                    )
                    Text(conversation.phone, color = PapiaColors.Muted, style = MaterialTheme.typography.bodySmall)
                }
                TextButton(onClick = onClose) { Text("Cerrar") }
            }
            Spacer(Modifier.height(10.dp))
            if (messages == null) {
                Box(Modifier.fillMaxWidth().padding(20.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp)
                }
            } else if (messages.isEmpty()) {
                EmptyState("Esta conversación no tiene mensajes.")
            } else {
                messages.takeLast(20).forEach { message ->
                    MessageBubble(message)
                }
            }
            Spacer(Modifier.height(10.dp))
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(8.dp))
                    .background(PapiaColors.Background)
                    .padding(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Responder desde el panel web por ahora", color = PapiaColors.Muted, modifier = Modifier.weight(1f))
                Text("→", color = PapiaColors.Blue, fontWeight = FontWeight.Bold)
            }
        }
    }
}

@Composable
fun MessageBubble(message: WhatsAppMessage) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        horizontalArrangement = if (message.inbound) Arrangement.Start else Arrangement.End,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth(0.82f)
                .clip(RoundedCornerShape(8.dp))
                .background(if (message.inbound) PapiaColors.Background else PapiaColors.Blue)
                .padding(10.dp),
        ) {
            Text(message.message.ifBlank { "[mensaje]" }, color = if (message.inbound) PapiaColors.Text else Color.White)
            Text(
                message.createdAt,
                color = if (message.inbound) PapiaColors.Muted else PapiaColors.LightMuted,
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
fun NotificationPill(count: Int) {
    if (count <= 0) return
    Row(
        modifier = Modifier
            .padding(end = 4.dp)
            .clip(RoundedCornerShape(99.dp))
            .background(PapiaColors.Success)
            .padding(horizontal = 10.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("WA", color = Color.White, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.width(6.dp))
        Text(count.toString(), color = Color.White, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
fun EmptyState(message: String) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 8.dp),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White),
    ) {
        Text(message, color = PapiaColors.Muted, modifier = Modifier.padding(16.dp))
    }
}

@Composable
fun ClientDetail(client: Client, onClose: () -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 12.dp),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = PapiaColors.Ink),
    ) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(client.fullName, color = Color.White, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                TextButton(onClick = onClose) { Text("Cerrar", color = Color.White) }
            }
            Text(client.email.ifBlank { "Sin email" }, color = PapiaColors.LightMuted)
            Text(client.phone.ifBlank { "Sin teléfono" }, color = PapiaColors.LightMuted)
            Spacer(Modifier.height(8.dp))
            Text(client.projectDetails.ifBlank { "Sin detalles del proyecto." }, color = Color.White)
            Spacer(Modifier.height(8.dp))
            Text("Total ${money(client.totalCost)} · pagado ${money(client.amountPaid)}", color = PapiaColors.LightMuted)
        }
    }
}

@Composable
fun SectionTitle(text: String) {
    Text(
        text,
        modifier = Modifier.padding(top = 8.dp, bottom = 8.dp),
        fontWeight = FontWeight.Bold,
        color = PapiaColors.Text,
    )
}

@Composable
fun Avatar(name: String) {
    val initials = name.split(" ").filter { it.isNotBlank() }.take(2).joinToString("") { it.first().uppercase() }.ifBlank { "P" }
    Box(
        modifier = Modifier
            .size(40.dp)
            .clip(CircleShape)
            .background(PapiaColors.BlueSoft),
        contentAlignment = Alignment.Center,
    ) {
        Text(initials, color = PapiaColors.Blue, fontWeight = FontWeight.Bold)
    }
}

@Composable
fun StatusDot(color: Color) {
    Box(
        modifier = Modifier
            .size(10.dp)
            .clip(CircleShape)
            .background(color),
    )
}

@Composable
fun PapiaTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = androidx.compose.material3.lightColorScheme(
            primary = PapiaColors.Blue,
            background = PapiaColors.Background,
            surface = Color.White,
        ),
        content = content,
    )
}

object PapiaColors {
    val Background = Color(0xFFF8FAFC)
    val Text = Color(0xFF111827)
    val Ink = Color(0xFF111827)
    val Muted = Color(0xFF64748B)
    val LightMuted = Color(0xFFCBD5E1)
    val Blue = Color(0xFF2563EB)
    val BlueSoft = Color(0xFFEFF6FF)
    val Success = Color(0xFF059669)
    val Danger = Color(0xFFDC2626)
    val Warning = Color(0xFFD97706)
}

fun money(value: Double): String = NumberFormat.getCurrencyInstance(Locale.US).format(value)

private const val WHATSAPP_CHANNEL_ID = "papia_whatsapp"

fun showWhatsAppNotification(
    context: Context,
    conversation: WhatsAppConversation?,
    newCount: Int,
    totalUnread: Int,
) {
    val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
        context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
    ) {
        return
    }
    val intent = Intent(context, MainActivity::class.java)
    val pendingIntent = PendingIntent.getActivity(
        context,
        0,
        intent,
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )
    val notification = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        android.app.Notification.Builder(context, WHATSAPP_CHANNEL_ID)
    } else {
        android.app.Notification.Builder(context)
    }
        .setSmallIcon(android.R.drawable.sym_action_chat)
        .setContentTitle("WhatsApp de ${conversation?.clientName?.ifBlank { conversation.phone } ?: "Papia CRM"}")
        .setContentText(conversation?.lastMessage?.ifBlank { "$newCount nuevo(s), $totalUnread sin leer" } ?: "$newCount nuevo(s), $totalUnread sin leer")
        .setContentIntent(pendingIntent)
        .setAutoCancel(true)
        .build()
    manager.notify(2001, notification)
}
