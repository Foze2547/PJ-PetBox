import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:image_picker/image_picker.dart';
import 'package:model_viewer_plus/model_viewer_plus.dart';
import 'package:petbox/config/app_config.dart';
import 'package:petbox/services/auth_service.dart';
import 'package:petbox/services/iot_service.dart';
import 'package:petbox/services/ai_server_service.dart';
import 'package:petbox/services/gpt_chat_service.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:speech_to_text/speech_to_text.dart' as stt;

class _SpeechToTextCoordinator {
  _SpeechToTextCoordinator._();

  static final stt.SpeechToText instance = stt.SpeechToText();
  static Future<bool>? _initializing;

  static Future<bool> ensureInitialized({
    stt.SpeechStatusListener? onStatus,
    stt.SpeechErrorListener? onError,
  }) async {
    if (instance.isAvailable) {
      instance.statusListener = onStatus;
      instance.errorListener = onError;
      return true;
    }

    final pending = _initializing;
    if (pending != null) {
      final available = await pending;
      instance.statusListener = onStatus;
      instance.errorListener = onError;
      return available;
    }

    final future = instance.initialize(onStatus: onStatus, onError: onError);
    _initializing = future;

    try {
      return await future;
    } finally {
      _initializing = null;
    }
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  int _selectedTab = 0;

  @override
  Widget build(BuildContext context) {
    final tabs = <Widget>[
      const _RobotHomeTabView(),
      const _HomeChatTabView(),
      const _HomeCameraTabView(),
      const _HomeRelaxTabView(),
    ];

    return Scaffold(
      backgroundColor: const Color(0xFFE8E6F7),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 14),
          child: Column(
            children: [
              Row(
                children: [
                  const Icon(
                    Icons.account_circle_outlined,
                    size: 30,
                    color: Color(0xFF2E2E38),
                  ),
                  const Spacer(),
                  const Icon(
                    Icons.notifications_none,
                    size: 28,
                    color: Color(0xFF4B4B55),
                  ),
                  const SizedBox(width: 12),
                  IconButton(
                    icon: const Icon(
                      Icons.settings_outlined,
                      size: 28,
                      color: Color(0xFF4B4B55),
                    ),
                    onPressed: () {
                      Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) => const SettingsPage(),
                        ),
                      );
                    },
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Expanded(
                child: IndexedStack(index: _selectedTab, children: tabs),
              ),
              const SizedBox(height: 10),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
                decoration: BoxDecoration(
                  color: const Color(0xFFF0F0EF),
                  borderRadius: BorderRadius.circular(34),
                  border: Border.all(color: const Color(0x22FFFFFF), width: 2),
                ),
                child: Row(
                  children: [
                    _HomeTab(
                      label: 'Robot',
                      icon: Icons.android_rounded,
                      active: _selectedTab == 0,
                      onTap: () => setState(() => _selectedTab = 0),
                    ),
                    _HomeTab(
                      label: 'Chat',
                      icon: Icons.chat_bubble_outline_rounded,
                      active: _selectedTab == 1,
                      onTap: () => setState(() => _selectedTab = 1),
                    ),
                    _HomeTab(
                      label: 'Camera',
                      icon: Icons.videocam_outlined,
                      active: _selectedTab == 2,
                      onTap: () => setState(() => _selectedTab = 2),
                    ),
                    _HomeTab(
                      label: 'Relax',
                      icon: Icons.nightlight_round,
                      active: _selectedTab == 3,
                      onTap: () => setState(() => _selectedTab = 3),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _HomeTab extends StatelessWidget {
  const _HomeTab({
    required this.label,
    required this.icon,
    required this.onTap,
    this.active = false,
  });

  final String label;
  final IconData icon;
  final VoidCallback onTap;
  final bool active;

  @override
  Widget build(BuildContext context) {
    final content = Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 26, color: const Color(0xFF3F4148)),
        const SizedBox(height: 4),
        Text(
          label,
          style: TextStyle(
            fontSize: 12,
            color: active ? const Color(0xFF0C85E5) : const Color(0xFF3F4148),
            fontWeight: active ? FontWeight.w700 : FontWeight.w500,
          ),
        ),
      ],
    );

    return Expanded(
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(28),
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 10),
          decoration: active
              ? BoxDecoration(
                  color: const Color(0xFFD6D7D6),
                  borderRadius: BorderRadius.circular(28),
                )
              : null,
          child: content,
        ),
      ),
    );
  }
}

class _RobotHomeTabView extends StatefulWidget {
  const _RobotHomeTabView();

  @override
  State<_RobotHomeTabView> createState() => _RobotHomeTabViewState();
}

class _RobotHomeTabViewState extends State<_RobotHomeTabView> {
  final stt.SpeechToText _speechToText = _SpeechToTextCoordinator.instance;
  final FlutterTts _flutterTts = FlutterTts();
  final List<_ChatItem> _history = [];

  bool _speechReady = false;
  bool _isListening = false;
  bool _isSending = false;
  bool _robotSpeaking = false;

  String _lastUserText = '';
  String _lastRobotText = 'Tap the mic and start talking.';

  @override
  void initState() {
    super.initState();
    _history.add(
      const _ChatItem(
        text: 'Hello! I am your Petbox chatbot. Ask me anything.',
        fromUser: false,
      ),
    );
    unawaited(_initVoiceFeatures());
  }

  @override
  void dispose() {
    _speechToText.stop();
    _flutterTts.stop();
    super.dispose();
  }

  Future<void> _initVoiceFeatures() async {
    try {
      final available = await _SpeechToTextCoordinator.ensureInitialized(
        onStatus: (status) {
          if (!mounted) return;
          if (status == 'done' || status == 'notListening') {
            setState(() => _isListening = false);
          }
        },
        onError: (_) {
          if (!mounted) return;
          setState(() => _isListening = false);
        },
      );

      _flutterTts.setStartHandler(() {
        if (!mounted) return;
        setState(() => _robotSpeaking = true);
      });
      _flutterTts.setCompletionHandler(() {
        if (!mounted) return;
        setState(() => _robotSpeaking = false);
      });
      _flutterTts.setCancelHandler(() {
        if (!mounted) return;
        setState(() => _robotSpeaking = false);
      });
      _flutterTts.setErrorHandler((_) {
        if (!mounted) return;
        setState(() => _robotSpeaking = false);
      });
      await _flutterTts.setSpeechRate(0.45);
      await _flutterTts.setPitch(1.0);

      if (!mounted) return;
      setState(() => _speechReady = available);
    } catch (e) {
      debugPrint('Robot voice init error: $e');
      if (!mounted) return;
      setState(() => _speechReady = false);
    }
  }

  Future<void> _toggleVoiceInput() async {
    if (_isSending) return;
    if (!_speechReady) {
      await _initVoiceFeatures();
    }
    if (!_speechReady) return;

    if (_isListening) {
      await _speechToText.stop();
      if (mounted) {
        setState(() => _isListening = false);
      }
      return;
    }

    try {
      await _speechToText.listen(
        partialResults: true,
        onResult: (result) {
          if (!mounted) return;
          setState(() => _lastUserText = result.recognizedWords);

          if (result.finalResult && result.recognizedWords.trim().isNotEmpty) {
            unawaited(_sendVoiceMessage(result.recognizedWords.trim()));
          }
        },
      );

      if (!mounted) return;
      setState(() => _isListening = _speechToText.isListening);
    } catch (e) {
      debugPrint('Robot listen error: $e');
      if (!mounted) return;
      setState(() => _isListening = false);
    }
  }

  Future<void> _sendVoiceMessage(String text) async {
    if (text.isEmpty || _isSending) return;

    if (_isListening) {
      await _speechToText.stop();
      if (mounted) {
        setState(() => _isListening = false);
      }
    }

    setState(() {
      _isSending = true;
      _lastUserText = text;
      _history.add(_ChatItem(text: text, fromUser: true));
    });

    try {
      final localReply = IotService.instance.processAiCommand(text);
      if (!localReply.startsWith('Unknown command.')) {
        if (!mounted) return;
        setState(() {
          _lastRobotText = localReply;
          _history.add(_ChatItem(text: localReply, fromUser: false));
        });
        await _speakReply(localReply);
        return;
      }

      final history = _history
          .take(_history.length - 1)
          .where((m) => !m.isError)
          .map(
            (m) => GptChatMessage(
              role: m.fromUser ? 'user' : 'assistant',
              content: m.text,
            ),
          )
          .toList();

      final reply = await GptChatService.instance.sendMessage(
        message: text,
        history: history,
      );

      if (!mounted) return;
      setState(() {
        _lastRobotText = reply;
        _history.add(_ChatItem(text: reply, fromUser: false));
      });
      await _speakReply(reply);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _lastRobotText = 'Error: $e';
        _history.add(_ChatItem(text: 'Error: $e', fromUser: false, isError: true));
      });
    } finally {
      if (mounted) {
        setState(() => _isSending = false);
      }
    }
  }

  Future<void> _speakReply(String text) async {
    await _flutterTts.stop();
    await _flutterTts.speak(text);
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        return SingleChildScrollView(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: ConstrainedBox(
            constraints: BoxConstraints(minHeight: constraints.maxHeight - 16),
            child: Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  SizedBox(
                    height: 300,
                    width: 300,
                    child: ModelViewer(
                      src: _robotSpeaking
                          ? 'assets/models/robot_app_speak.glb'
                          : 'assets/models/robot_app_no_speak.glb',
                      alt: 'Petbox robot',
                      poster: 'lib/image/Group 67.png',
                      loading: Loading.auto,
                      reveal: Reveal.auto,
                      ar: false,
                      autoRotate: true,
                      autoRotateDelay: 500,
                      rotationPerSecond: '18deg',
                      cameraControls: true,
                      disableZoom: true,
                      disablePan: true,
                      interactionPrompt: InteractionPrompt.none,
                    ),
                  ),
                  const SizedBox(height: 56),
                  InkWell(
                    onTap: (_speechReady && !_isSending) ? _toggleVoiceInput : null,
                    borderRadius: BorderRadius.circular(40),
                    child: Container(
                      width: 78,
                      height: 78,
                      decoration: BoxDecoration(
                        color: _isListening
                            ? Colors.redAccent
                            : const Color(0xFF0588EA),
                        shape: BoxShape.circle,
                        boxShadow: const [
                          BoxShadow(
                            color: Color(0x330588EA),
                            blurRadius: 16,
                            offset: Offset(0, 6),
                          ),
                        ],
                      ),
                      child: Icon(
                        _isListening ? Icons.mic : Icons.mic_none_rounded,
                        color: Colors.white,
                        size: 40,
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    _isSending
                        ? 'Thinking...'
                        : (_isListening ? 'Listening...' : 'Tap To Talk'),
                    style: const TextStyle(
                      fontSize: 26 / 1.2,
                      color: Color(0xFF7B7D88),
                    ),
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 12),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 20),
                    child: Column(
                      children: [
                        if (_lastUserText.isNotEmpty)
                          Container(
                            width: double.infinity,
                            padding: const EdgeInsets.symmetric(
                              horizontal: 12,
                              vertical: 8,
                            ),
                            margin: const EdgeInsets.only(bottom: 8),
                            decoration: BoxDecoration(
                              color: const Color(0xFFE3DFEA),
                              borderRadius: BorderRadius.circular(12),
                            ),
                            child: Text(
                              'You: $_lastUserText',
                              softWrap: true,
                            ),
                          ),
                        Container(
                          width: double.infinity,
                          padding: const EdgeInsets.symmetric(
                            horizontal: 12,
                            vertical: 8,
                          ),
                          decoration: BoxDecoration(
                            color: const Color(0xFFDDECFB),
                            borderRadius: BorderRadius.circular(12),
                          ),
                          child: Text(
                            'Robot: $_lastRobotText',
                            softWrap: true,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

class _HomeChatTabView extends StatelessWidget {
  const _HomeChatTabView();
  @override
  Widget build(BuildContext context) {
    return const _GptChatBot(
      title: 'AI Assistant',
      greeting: 'Hello! I am your Petbox chatbot. Ask me anything.',
      showAppBar: false,
    );
  }
}

class _GptChatBot extends StatefulWidget {
  const _GptChatBot({
    required this.title,
    required this.greeting,
    required this.showAppBar,
  });
  final String title;
  final String greeting;
  final bool showAppBar;
  @override
  State<_GptChatBot> createState() => _GptChatBotState();
}

class _GptChatBotState extends State<_GptChatBot> {
  final TextEditingController _controller = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  final List<_ChatItem> _messages = [];
  bool _isSending = false;

  @override
  void initState() {
    super.initState();
    _messages.add(_ChatItem(text: widget.greeting, fromUser: false));
  }

  @override
  void dispose() {
    _controller.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final text = _controller.text.trim();
    if (text.isEmpty || _isSending) return;

    setState(() {
      _isSending = true;
      _messages.add(_ChatItem(text: text, fromUser: true));
    });
    _controller.clear();
    _scrollToBottom();

    try {
      final history = _messages
          .take(_messages.length - 1)
          .where((m) => !m.isError)
          .map(
            (m) => GptChatMessage(
              role: m.fromUser ? 'user' : 'assistant',
              content: m.text,
            ),
          )
          .toList();
      final reply = await GptChatService.instance.sendMessage(
        message: text,
        history: history,
      );
      if (!mounted) return;
      setState(() {
        _messages.add(_ChatItem(text: reply, fromUser: false));
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _messages.add(
          _ChatItem(text: 'Error: $e', fromUser: false, isError: true),
        );
      });
    } finally {
      if (mounted) {
        setState(() {
          _isSending = false;
        });
        _scrollToBottom();
      }
    }
  }

  void _clearChat() {
    setState(() {
      _messages
        ..clear()
        ..add(_ChatItem(text: widget.greeting, fromUser: false));
    });
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) return;
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent + 80,
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    final chatBody = Column(
      children: [
        Row(
          children: [
            Text(
              widget.title,
              style: const TextStyle(
                fontSize: 24,
                fontWeight: FontWeight.w700,
                color: Color(0xFF2D2E36),
              ),
            ),
            const Spacer(),
            IconButton(
              onPressed: _isSending ? null : _clearChat,
              icon: const Icon(Icons.delete_outline, color: Color(0xFF4D4E57)),
            ),
          ],
        ),
        const SizedBox(height: 8),
        Expanded(
          child: Container(
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: Colors.white.withValues(alpha: 0.6),
              borderRadius: BorderRadius.circular(14),
            ),
            child: ListView.builder(
              controller: _scrollController,
              itemCount: _messages.length,
              itemBuilder: (context, index) {
                final msg = _messages[index];
                return _aiBubble(msg.text, msg.fromUser, msg.isError);
              },
            ),
          ),
        ),
        const SizedBox(height: 10),
        Row(
          children: [
            Expanded(
              child: Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 14,
                  vertical: 4,
                ),
                decoration: BoxDecoration(
                  color: const Color(0xFFE7E4EE),
                  borderRadius: BorderRadius.circular(26),
                ),
                child: Row(
                  children: [
                    const Icon(
                      Icons.message_outlined,
                      color: Color(0xFF4F5058),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: TextField(
                        controller: _controller,
                        enabled: !_isSending,
                        decoration: const InputDecoration(
                          hintText: 'Type your message...',
                          border: InputBorder.none,
                        ),
                        onSubmitted: (_) => _send(),
                      ),
                    ),
                    IconButton(
                      onPressed: _isSending ? null : _send,
                      icon: _isSending
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(
                              Icons.send_rounded,
                              color: Color(0xFF4F5058),
                            ),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ],
    );
    if (!widget.showAppBar) {
      return chatBody;
    }
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.title),
        backgroundColor: AppColors.primaryBlue,
        foregroundColor: Colors.white,
      ),
      body: Padding(
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 16),
        child: chatBody,
      ),
    );
  }

  Widget _aiBubble(String text, bool fromUser, bool isError) {
    return Align(
      alignment: fromUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: BoxDecoration(
          color: isError
              ? const Color(0xFFFFE3E3)
              : (fromUser ? const Color(0xFF69637C) : const Color(0xFFE3DFEA)),
          borderRadius: BorderRadius.circular(16),
        ),
        child: Text(
          text,
          style: TextStyle(
            color: fromUser ? Colors.white : const Color(0xFF41424B),
            fontSize: 16 / 1.2,
          ),
        ),
      ),
    );
  }
}

class _HomeCameraTabView extends StatefulWidget {
  const _HomeCameraTabView();

  @override
  State<_HomeCameraTabView> createState() => _HomeCameraTabViewState();
}

class _HomeCameraTabViewState extends State<_HomeCameraTabView> {
  final RobotControl _robotControl = RobotControl();
  final TextEditingController _serverIpController = TextEditingController();
  WebSocket? _cameraSocket;
  Timer? _reconnectTimer;
  Uint8List? _latestFrameBytes;
  List<_CameraDetection> _latestDetections = const <_CameraDetection>[];
  int? _latestFrameId;
  bool _isCameraConnected = false;
  bool _isCameraConnecting = false;
  String _cameraStatus = 'Enter this PC IP to view the stream';
  int _cameraSessionId = 0;

  @override
  void initState() {
    super.initState();
    _restoreCameraServerIp();
  }

  @override
  void dispose() {
    _reconnectTimer?.cancel();
    _cameraSocket?.close();
    _serverIpController.dispose();
    super.dispose();
  }

  Future<void> _restoreCameraServerIp() async {
    final savedIp = await AiServerService.instance.getHost();
    if (!mounted) return;
    _serverIpController.text = savedIp;
    if (savedIp.isNotEmpty) {
      unawaited(_connectToCamera());
    }
  }

  Future<void> _persistCameraServerIp(String ipAddress) async {
    await AiServerService.instance.setHost(ipAddress);
  }

  Future<void> _closeCameraSocket() async {
    final socket = _cameraSocket;
    _cameraSocket = null;
    await socket?.close();
  }

  Future<void> _connectToCamera() async {
    final host = _serverIpController.text.trim();
    if (host.isEmpty) {
      setState(() {
        _cameraStatus = 'Enter the PC IP that runs the AI server';
      });
      return;
    }

    _reconnectTimer?.cancel();
    await _persistCameraServerIp(host);

    final sessionId = ++_cameraSessionId;
    await _closeCameraSocket();

    if (!mounted) return;
    setState(() {
      _isCameraConnecting = true;
      _isCameraConnected = false;
      _cameraStatus = 'Connecting to ';
    });

    try {
      final socketUrl = await AiServerService.instance.getLiveWebSocketUrl();
      final socket = await WebSocket.connect(
        socketUrl,
      ).timeout(const Duration(seconds: 5));
      socket.pingInterval = const Duration(seconds: 10);

      if (!mounted || sessionId != _cameraSessionId) {
        await socket.close();
        return;
      }

      _cameraSocket = socket;
      socket.listen(
        _handleCameraMessage,
        onDone: () => _handleCameraDisconnected(sessionId),
        onError: (_) => _handleCameraDisconnected(sessionId),
        cancelOnError: true,
      );

      setState(() {
        _isCameraConnecting = false;
        _isCameraConnected = true;
        _cameraStatus = 'Connected over Wi-Fi';
      });
    } catch (_) {
      if (!mounted || sessionId != _cameraSessionId) return;
      setState(() {
        _isCameraConnecting = false;
        _isCameraConnected = false;
        _cameraStatus = 'Connect failed. Check the PC IP and server.';
      });
      _scheduleReconnect();
    }
  }

  Future<void> _disconnectCamera() async {
    _reconnectTimer?.cancel();
    _cameraSessionId++;
    await _closeCameraSocket();
    if (!mounted) return;
    setState(() {
      _isCameraConnecting = false;
      _isCameraConnected = false;
      _cameraStatus = 'Disconnected';
    });
  }

  void _scheduleReconnect() {
    if (_serverIpController.text.trim().isEmpty ||
        _reconnectTimer?.isActive == true) {
      return;
    }
    _reconnectTimer = Timer(const Duration(seconds: 2), () {
      if (mounted && !_isCameraConnected) {
        unawaited(_connectToCamera());
      }
    });
  }

  void _handleCameraDisconnected(int sessionId) {
    if (!mounted || sessionId != _cameraSessionId) return;
    setState(() {
      _isCameraConnecting = false;
      _isCameraConnected = false;
      _cameraStatus = 'Connection lost. Retrying';
    });
    _scheduleReconnect();
  }

  void _handleCameraMessage(dynamic data) {
    if (data is! String) return;

    final decoded = jsonDecode(data);
    if (decoded is! Map<String, dynamic>) return;

    final packet = _LiveFramePacket.fromJson(decoded);
    if (!mounted) return;

    setState(() {
      _latestFrameId = packet.frameId;
      _latestDetections = packet.detections;
      _latestFrameBytes = base64Decode(packet.jpegB64);
    });
  }

  Future<void> _sendRobotCommand(String command, String _label) async {
    await _robotControl.sendCommand(command);
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.only(top: 8),
      children: [
        const Text(
          'Live camera view',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 12),
        Container(
          height: 300,
          clipBehavior: Clip.antiAlias,
          decoration: BoxDecoration(
            color: const Color(0xFF101317),
            borderRadius: BorderRadius.circular(16),
          ),
          child: Stack(
            fit: StackFit.expand,
            children: [
              if (_latestFrameBytes != null)
                Image.memory(
                  _latestFrameBytes!,
                  fit: BoxFit.cover,
                  gaplessPlayback: true,
                )
              else
                const Center(
                  child: Icon(
                    Icons.videocam_outlined,
                    size: 80,
                    color: Colors.white38,
                  ),
                ),
              if (_latestFrameBytes == null)
                Center(
                  child: Padding(
                    padding: const EdgeInsets.only(top: 104),
                    child: Text(
                      _cameraStatus,
                      textAlign: TextAlign.center,
                      style: const TextStyle(color: Colors.white70),
                    ),
                  ),
                ),
              if (_latestFrameBytes != null)
                Positioned.fill(
                  child: CustomPaint(
                    painter: _DetectionOverlayPainter(
                      detections: _latestDetections,
                    ),
                  ),
                ),
              Positioned(
                left: 12,
                top: 12,
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 10,
                    vertical: 6,
                  ),
                  decoration: BoxDecoration(
                    color: const Color(0xB20B0F12),
                    borderRadius: BorderRadius.circular(999),
                  ),
                ),
              ),
              Positioned(
                right: 12,
                top: 12,
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 10,
                    vertical: 6,
                  ),
                  decoration: BoxDecoration(
                    color: const Color(0xB20B0F12),
                    borderRadius: BorderRadius.circular(999),
                  ),
                  child: Text(
                    '${_latestDetections.length} objects',
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        const Text(
          'Robot Control',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 10),
        Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              _DirectionControlButton(
                label: 'Fwd',
                icon: Icons.keyboard_arrow_up,
                onTap: () => _sendRobotCommand('forward', 'Fwd'),
              ),
              const SizedBox(height: 10),
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  _DirectionControlButton(
                    label: 'Left',
                    icon: Icons.keyboard_arrow_left,
                    compact: true,
                    onTap: () => _sendRobotCommand('left', 'Left'),
                  ),
                  const SizedBox(width: 16),
                  _StopControlButton(
                    onTap: () => _sendRobotCommand('stop', 'Stop'),
                  ),
                  const SizedBox(width: 16),
                  _DirectionControlButton(
                    label: 'Right',
                    icon: Icons.keyboard_arrow_right,
                    compact: true,
                    onTap: () => _sendRobotCommand('right', 'Right'),
                  ),
                ],
              ),
              const SizedBox(height: 10),
              _DirectionControlButton(
                label: 'Back',
                icon: Icons.keyboard_arrow_down,
                onTap: () => _sendRobotCommand('backward', 'Back'),
              ),
            ],
          ),
        ),
        const SizedBox(height: 6),
      ],
    );
  }
}

class _LiveFramePacket {
  const _LiveFramePacket({
    required this.frameId,
    required this.jpegB64,
    required this.detections,
  });

  factory _LiveFramePacket.fromJson(Map<String, dynamic> json) {
    final rawDetections = json['detections'];
    final detections = rawDetections is List
        ? rawDetections
              .whereType<Map<String, dynamic>>()
              .map(_CameraDetection.fromJson)
              .toList()
        : const <_CameraDetection>[];

    return _LiveFramePacket(
      frameId: (json['frame_id'] as num?)?.toInt() ?? 0,
      jpegB64: json['jpeg_b64']?.toString() ?? '',
      detections: detections,
    );
  }

  final int frameId;
  final String jpegB64;
  final List<_CameraDetection> detections;
}

class _CameraDetection {
  const _CameraDetection({
    required this.label,
    required this.confidence,
    required this.x1,
    required this.y1,
    required this.x2,
    required this.y2,
  });

  factory _CameraDetection.fromJson(Map<String, dynamic> json) {
    double toDouble(String key) => (json[key] as num?)?.toDouble() ?? 0;

    return _CameraDetection(
      label: json['label']?.toString() ?? 'Object',
      confidence: toDouble('confidence'),
      x1: toDouble('x1'),
      y1: toDouble('y1'),
      x2: toDouble('x2'),
      y2: toDouble('y2'),
    );
  }

  final String label;
  final double confidence;
  final double x1;
  final double y1;
  final double x2;
  final double y2;
}

class _DetectionOverlayPainter extends CustomPainter {
  const _DetectionOverlayPainter({required this.detections});

  final List<_CameraDetection> detections;

  @override
  void paint(Canvas canvas, Size size) {
    final boxPaint = Paint()
      ..color = const Color(0xFF7EF9A9)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.5;
    final labelPaint = Paint()..color = const Color(0xCC071410);
    final textPainter = TextPainter(textDirection: TextDirection.ltr);

    for (final detection in detections) {
      final left = detection.x1 * size.width;
      final top = detection.y1 * size.height;
      final width = (detection.x2 - detection.x1) * size.width;
      final height = (detection.y2 - detection.y1) * size.height;
      final rect = Rect.fromLTWH(left, top, width, height);
      canvas.drawRect(rect, boxPaint);

      final confidencePercent = (detection.confidence * 100)
          .clamp(0, 100)
          .toStringAsFixed(0);
      final label = '${detection.label} $confidencePercent%';
      textPainter.text = TextSpan(
        text: label,
        style: const TextStyle(
          color: Color(0xFFDFFFF0),
          fontSize: 12,
          fontWeight: FontWeight.w700,
        ),
      );
      textPainter.layout();

      final labelTop = (top - textPainter.height - 10)
          .clamp(0, size.height - textPainter.height - 6)
          .toDouble();
      final labelRect = RRect.fromRectAndRadius(
        Rect.fromLTWH(
          left,
          labelTop,
          textPainter.width + 12,
          textPainter.height + 6,
        ),
        const Radius.circular(8),
      );
      canvas.drawRRect(labelRect, labelPaint);
      textPainter.paint(canvas, Offset(left + 6, labelTop + 3));
    }
  }

  @override
  bool shouldRepaint(covariant _DetectionOverlayPainter oldDelegate) {
    return oldDelegate.detections != detections;
  }
}

class _HomeRelaxTabView extends StatefulWidget {
  const _HomeRelaxTabView();

  @override
  State<_HomeRelaxTabView> createState() => _HomeRelaxTabViewState();
}

class _HomeRelaxTabViewState extends State<_HomeRelaxTabView> {
  final List<_RelaxProgram> _programs = const [
    _RelaxProgram(
      id: 'music',
      title: 'Music',
      description:
          'Soft background music + calm device profile for winding down.',
      minutes: 23,
    ),
    _RelaxProgram(
      id: 'podcast',
      title: 'Podcast',
      description:
          'Voice-friendly profile with mild airflow and stable robot speed.',
      minutes: 23,
    ),
  ];

  String? _activeProgramId;
  int _remainingSeconds = 0;
  Timer? _timer;

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  void _startProgram(_RelaxProgram program) {
    _timer?.cancel();
    _applyScene(program.id);
    setState(() {
      _activeProgramId = program.id;
      _remainingSeconds = program.minutes * 60;
    });

    _timer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (!mounted) return;
      if (_remainingSeconds <= 1) {
        timer.cancel();
        setState(() {
          _remainingSeconds = 0;
          _activeProgramId = null;
        });
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${program.title} session completed')),
        );
        return;
      }
      setState(() {
        _remainingSeconds--;
      });
    });
  }

  void _stopProgram() {
    _timer?.cancel();
    setState(() {
      _activeProgramId = null;
      _remainingSeconds = 0;
    });
  }

  void _applyScene(String programId) {
    final iotService = IotService.instance;
    if (programId == 'music') {
      iotService.setAutoMode(true);
      iotService.setLight(true);
      iotService.setFan(false);
      iotService.setPump(false);
      iotService.setRobotSpeed(18);
      return;
    }
    if (programId == 'podcast') {
      iotService.setAutoMode(true);
      iotService.setLight(false);
      iotService.setFan(true);
      iotService.setPump(false);
      iotService.setRobotSpeed(28);
    }
  }

  String _formatTime(int totalSeconds) {
    final m = (totalSeconds ~/ 60).toString().padLeft(2, '0');
    final s = (totalSeconds % 60).toString().padLeft(2, '0');
    return '$m:$s';
  }

  @override
  Widget build(BuildContext context) {
    final iotService = IotService.instance;
    return ValueListenableBuilder<DeviceState>(
      valueListenable: iotService.stateNotifier,
      builder: (context, state, _) {
        return ListView(
          padding: const EdgeInsets.only(top: 8),
          children: [
            const Text(
              'Relax',
              style: TextStyle(
                fontSize: 34 / 1.2,
                fontWeight: FontWeight.w500,
                color: Color(0xFF2F3038),
              ),
            ),
            const SizedBox(height: 12),
            ..._programs.map((program) {
              final active = _activeProgramId == program.id;
              return Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: _RelaxItemCard(
                  program: program,
                  active: active,
                  trailingText: active ? _formatTime(_remainingSeconds) : null,
                  onTap: () => active ? _stopProgram() : _startProgram(program),
                ),
              );
            }),
          ],
        );
      },
    );
  }
}

class _RelaxProgram {
  const _RelaxProgram({
    required this.id,
    required this.title,
    required this.description,
    required this.minutes,
  });

  final String id;
  final String title;
  final String description;
  final int minutes;
}

class _RelaxItemCard extends StatelessWidget {
  const _RelaxItemCard({
    required this.program,
    required this.active,
    required this.onTap,
    this.trailingText,
  });

  final _RelaxProgram program;
  final bool active;
  final VoidCallback onTap;
  final String? trailingText;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: 118,
          height: 118,
          decoration: BoxDecoration(
            color: const Color(0xFFE4E1EA),
            borderRadius: BorderRadius.circular(16),
          ),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: const [
              Icon(
                Icons.change_history_rounded,
                size: 34,
                color: Color(0xFFB2ADB9),
              ),
              SizedBox(height: 2),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(Icons.brightness_1, size: 26, color: Color(0xFFB2ADB9)),
                  SizedBox(width: 4),
                  Icon(
                    Icons.square_rounded,
                    size: 34,
                    color: Color(0xFFB2ADB9),
                  ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                program.title,
                style: const TextStyle(
                  fontSize: 40 / 1.2,
                  color: Color(0xFF2F3038),
                ),
              ),
              const SizedBox(height: 4),
              Text(
                program.description,
                style: const TextStyle(color: Color(0xFF4C4D56), height: 1.35),
              ),
              const SizedBox(height: 10),
              Row(
                children: [
                  const Icon(
                    Icons.add_circle_outline,
                    size: 24,
                    color: Color(0xFF2F3038),
                  ),
                  const SizedBox(width: 6),
                  Text(
                    active ? 'Playing' : 'Today',
                    style: const TextStyle(color: Color(0xFF4C4D56)),
                  ),
                  Text(
                    active
                        ? ' - ${trailingText ?? ''}'
                        : ' ${program.minutes} min',
                    style: const TextStyle(color: Color(0xFF4C4D56)),
                  ),
                  const Spacer(),
                  IconButton(
                    onPressed: onTap,
                    icon: Icon(
                      active ? Icons.stop_rounded : Icons.play_arrow_rounded,
                      color: const Color(0xFF2F3038),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class RobotStatusPage extends StatelessWidget {
  const RobotStatusPage({super.key});

  @override
  Widget build(BuildContext context) {
    final iotService = IotService.instance;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Robot Status'),
        backgroundColor: AppColors.primaryIndigo,
        foregroundColor: Colors.white,
      ),
      body: ValueListenableBuilder<DeviceState>(
        valueListenable: iotService.stateNotifier,
        builder: (context, state, _) {
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              Container(
                padding: const EdgeInsets.all(18),
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(14),
                  boxShadow: const [
                    BoxShadow(color: Color(0x22000000), blurRadius: 10),
                  ],
                ),
                child: Column(
                  children: [
                    const Icon(
                      Icons.smart_toy,
                      size: 84,
                      color: AppColors.primaryIndigo,
                    ),
                    const SizedBox(height: 8),
                    Text(
                      state.autoMode ? 'Auto Mode' : 'Manual Mode',
                      style: const TextStyle(
                        fontSize: 18,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Text('Robot speed: ${state.robotSpeed}%'),
                  ],
                ),
              ),
              const SizedBox(height: 16),
              _statusTile('Light', state.lightOn),
              _statusTile('Fan', state.fanOn),
              _statusTile('Water Pump', state.pumpOn),
              _statusTile('Auto Mode', state.autoMode),
            ],
          );
        },
      ),
    );
  }

  Widget _statusTile(String title, bool enabled) {
    return Card(
      child: ListTile(
        leading: Icon(
          enabled ? Icons.check_circle : Icons.cancel,
          color: enabled ? AppColors.primaryGreen : Colors.redAccent,
        ),
        title: Text(title),
        trailing: Text(enabled ? 'ON' : 'OFF'),
      ),
    );
  }
}

class RobotControlPage extends StatelessWidget {
  const RobotControlPage({super.key, this.initialToIot = false});

  final bool initialToIot;

  @override
  Widget build(BuildContext context) {
    final iotService = IotService.instance;
    return Scaffold(
      appBar: AppBar(
        title: Text(initialToIot ? 'IoT Device Control' : 'Robot Control'),
        backgroundColor: AppColors.primaryGreen,
        foregroundColor: Colors.white,
      ),
      body: ValueListenableBuilder<DeviceState>(
        valueListenable: iotService.stateNotifier,
        builder: (context, state, _) {
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              SwitchListTile(
                value: state.autoMode,
                onChanged: iotService.setAutoMode,
                title: const Text('Auto Mode'),
              ),
              const SizedBox(height: 6),
              const Text('Robot Speed'),
              Slider(
                value: state.robotSpeed.toDouble(),
                min: 0,
                max: 100,
                divisions: 20,
                label: '${state.robotSpeed}%',
                onChanged: (v) => iotService.setRobotSpeed(v.round()),
              ),
              const SizedBox(height: 6),
              SwitchListTile(
                value: state.lightOn,
                onChanged: iotService.setLight,
                title: const Text('Light'),
              ),
              SwitchListTile(
                value: state.fanOn,
                onChanged: iotService.setFan,
                title: const Text('Fan'),
              ),
              SwitchListTile(
                value: state.pumpOn,
                onChanged: iotService.setPump,
                title: const Text('Water Pump'),
              ),
            ],
          );
        },
      ),
    );
  }
}

class AiChatPage extends StatelessWidget {
  const AiChatPage({super.key});
  @override
  Widget build(BuildContext context) {
    return const _GptChatBot(
      title: 'AI Chat Bot',
      greeting: 'Hello! I am your Petbox chatbot. Ask me anything.',
      showAppBar: true,
    );
  }
}

class _ChatItem {
  const _ChatItem({
    required this.text,
    required this.fromUser,
    this.isError = false,
  });
  final String text;
  final bool fromUser;
  final bool isError;
}

class CameraPage extends StatefulWidget {
  const CameraPage({super.key});

  @override
  State<CameraPage> createState() => _CameraPageState();
}

class _CameraPageState extends State<CameraPage> {
  final RobotControl _robotControl = RobotControl();

  Future<void> _sendRobotCommand(String command, String _label) async {
    await _robotControl.sendCommand(command);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Camera'),
        backgroundColor: AppColors.primaryIndigo,
        foregroundColor: Colors.white,
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Container(
            height: 220,
            decoration: BoxDecoration(
              color: Colors.black12,
              borderRadius: BorderRadius.circular(16),
            ),
            child: const Center(
              child: Icon(
                Icons.videocam_outlined,
                size: 72,
                color: Colors.black45,
              ),
            ),
          ),
          const SizedBox(height: 14),
          const Text(
            'Live view placeholder',
            style: TextStyle(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 18),
          const Text(
            'Robot Control',
            style: TextStyle(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 10),
          Center(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                _DirectionControlButton(
                  label: 'Fwd',
                  icon: Icons.keyboard_arrow_up,
                  onTap: () => _sendRobotCommand('forward', 'Fwd'),
                ),
                const SizedBox(height: 10),
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    _DirectionControlButton(
                      label: 'Left',
                      icon: Icons.keyboard_arrow_left,
                      compact: true,
                      onTap: () => _sendRobotCommand('left', 'Left'),
                    ),
                    const SizedBox(width: 16),
                    _StopControlButton(
                      onTap: () => _sendRobotCommand('stop', 'Stop'),
                    ),
                    const SizedBox(width: 16),
                    _DirectionControlButton(
                      label: 'Right',
                      icon: Icons.keyboard_arrow_right,
                      compact: true,
                      onTap: () => _sendRobotCommand('right', 'Right'),
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                _DirectionControlButton(
                  label: 'Back',
                  icon: Icons.keyboard_arrow_down,
                  onTap: () => _sendRobotCommand('backward', 'Back'),
                ),
              ],
            ),
          ),
          const SizedBox(height: 18),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton.icon(
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => const RobotControlPage(),
                ),
              ),
              icon: const Icon(Icons.tune),
              label: const Text('Go to Robot Control'),
              style: ElevatedButton.styleFrom(
                backgroundColor: AppColors.primaryGreen,
                foregroundColor: Colors.white,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _DirectionControlButton extends StatelessWidget {
  const _DirectionControlButton({
    required this.label,
    required this.icon,
    required this.onTap,
    this.compact = false,
  });

  final String label;
  final IconData icon;
  final VoidCallback onTap;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    return ElevatedButton.icon(
      onPressed: onTap,
      icon: Icon(icon),
      label: Text(label),
      style: ElevatedButton.styleFrom(
        minimumSize: compact ? const Size(100, 48) : const Size(120, 48),
        backgroundColor: AppColors.primaryIndigo,
        foregroundColor: Colors.white,
      ),
    );
  }
}

class _StopControlButton extends StatelessWidget {
  const _StopControlButton({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return ElevatedButton(
      onPressed: onTap,
      style: ElevatedButton.styleFrom(
        minimumSize: const Size(56, 56),
        shape: const CircleBorder(),
        backgroundColor: Colors.redAccent,
        foregroundColor: Colors.white,
        padding: EdgeInsets.zero,
      ),
      child: const Icon(Icons.stop_rounded),
    );
  }
}

class RelaxPage extends StatelessWidget {
  const RelaxPage({super.key});

  @override
  Widget build(BuildContext context) {
    final iotService = IotService.instance;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Relax'),
        backgroundColor: AppColors.primaryOrange,
        foregroundColor: Colors.white,
      ),
      body: ValueListenableBuilder<DeviceState>(
        valueListenable: iotService.stateNotifier,
        builder: (context, state, _) {
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              const Text(
                'Relax Scene',
                style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 10),
              const Text(
                'Enjoy a relaxing environment with optimal settings for your comfort.',
              ),
              const SizedBox(height: 18),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: () {
                    iotService.setLight(true);
                    iotService.setFan(false);
                    iotService.setPump(false);
                    iotService.setAutoMode(true);
                    iotService.setRobotSpeed(20);
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('Relax mode activated')),
                    );
                  },
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.primaryOrange,
                    foregroundColor: Colors.white,
                  ),
                  child: const Text('Activate Relax Mode'),
                ),
              ),
              const SizedBox(height: 16),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Text(
                    'Current status: light ${state.lightOn ? 'on' : 'off'}, '
                    'fan ${state.fanOn ? 'on' : 'off'}, '
                    'pump ${state.pumpOn ? 'on' : 'off'}, '
                    'speed ${state.robotSpeed}%',
                  ),
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class SettingsPage extends StatelessWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF2F2F2),
      appBar: AppBar(
        backgroundColor: const Color(0xFFF2F2F2),
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_ios_new, color: Colors.black87),
          onPressed: () => Navigator.of(context).pop(),
        ),
        centerTitle: true,
        title: const Text(
          'Settings',
          style: TextStyle(color: Colors.black, fontWeight: FontWeight.w700),
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        children: [
          const _SettingsHeader('Account'),
          _SettingsGroup(
            items: [
              _SettingsItem(
                icon: Icons.person_outline,
                label: 'Edit profile',
                onTap: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => const EditProfilePage(),
                  ),
                ),
              ),
              _SettingsItem(
                icon: Icons.shield_outlined,
                label: 'Security',
                onTap: () => _showComingSoon(context),
              ),
              _SettingsItem(
                icon: Icons.notifications_none,
                label: 'Notifications',
                onTap: () => _showComingSoon(context),
              ),
              _SettingsItem(
                icon: Icons.lock_outline,
                label: 'Privacy',
                onTap: () => _showComingSoon(context),
              ),
            ],
          ),
          const SizedBox(height: 12),
          const _SettingsHeader('Home'),
          _SettingsGroup(
            items: [
              _SettingsItem(
                icon: Icons.home_outlined,
                label: 'IoT',
                onTap: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(builder: (_) => const IotHomePage()),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          const _SettingsHeader('Support & About'),
          _SettingsGroup(
            items: [
              _SettingsItem(
                icon: Icons.wallet_outlined,
                label: 'My Subscription',
                onTap: () => _showComingSoon(context),
              ),
              _SettingsItem(
                icon: Icons.help_outline,
                label: 'Help & Support',
                onTap: () => _showComingSoon(context),
              ),
              _SettingsItem(
                icon: Icons.info_outline,
                label: 'Terms and Policies',
                onTap: () => _showComingSoon(context),
              ),
            ],
          ),
          const SizedBox(height: 12),
          const _SettingsHeader('Actions'),
          _SettingsGroup(
            items: [
              _SettingsItem(
                icon: Icons.flag_outlined,
                label: 'Report a problem',
                onTap: () => _showComingSoon(context),
              ),
              _SettingsItem(
                icon: Icons.person_add_alt_1_outlined,
                label: 'Add account',
                onTap: () => _showComingSoon(context),
              ),
              _SettingsItem(
                icon: Icons.logout,
                label: 'Log out',
                onTap: AuthService.signOut,
              ),
            ],
          ),
        ],
      ),
    );
  }

  void _showComingSoon(BuildContext context) {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('This feature is coming soon')),
    );
  }
}

class _SettingsHeader extends StatelessWidget {
  const _SettingsHeader(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Text(
        text,
        style: const TextStyle(fontSize: 30 / 1.2, fontWeight: FontWeight.w700),
      ),
    );
  }
}

class _SettingsGroup extends StatelessWidget {
  const _SettingsGroup({required this.items});

  final List<_SettingsItem> items;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 6),
      decoration: BoxDecoration(
        color: const Color(0xFFE8E8EC),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        children: items
            .map(
              (item) => ListTile(
                leading: Icon(item.icon, color: const Color(0xFF5A5355)),
                title: Text(
                  item.label,
                  style: const TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                onTap: item.onTap,
              ),
            )
            .toList(),
      ),
    );
  }
}

class _SettingsItem {
  const _SettingsItem({
    required this.icon,
    required this.label,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final VoidCallback onTap;
}

class EditProfilePage extends StatefulWidget {
  const EditProfilePage({super.key});

  @override
  State<EditProfilePage> createState() => _EditProfilePageState();
}

class _EditProfilePageState extends State<EditProfilePage> {
  final _name = TextEditingController(text: 'Melissa Peters');
  final _email = TextEditingController(text: 'melpeters@gmail.com');
  final _password = TextEditingController(text: '************');
  final _dob = TextEditingController(text: '23/05/1995');
  final _country = TextEditingController(text: 'Nigeria');

  @override
  void dispose() {
    _name.dispose();
    _email.dispose();
    _password.dispose();
    _dob.dispose();
    _country.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF2F2F2),
      appBar: AppBar(
        backgroundColor: const Color(0xFFF2F2F2),
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_ios_new, color: Colors.black87),
          onPressed: () => Navigator.of(context).pop(),
        ),
        centerTitle: true,
        title: const Text(
          'Edit Profile',
          style: TextStyle(color: Colors.black, fontWeight: FontWeight.w700),
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 6, 20, 20),
        children: [
          Center(
            child: Stack(
              children: [
                Container(
                  width: 160,
                  height: 160,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: const Color(0xFF4E5083),
                      width: 2,
                    ),
                    color: Colors.white,
                  ),
                  child: const Icon(
                    Icons.person,
                    size: 90,
                    color: Color(0xFF8A8AA0),
                  ),
                ),
                Positioned(
                  right: 4,
                  bottom: 8,
                  child: CircleAvatar(
                    radius: 14,
                    backgroundColor: const Color(0xFF4E5083),
                    child: IconButton(
                      onPressed: () {
                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(
                            content: Text('Camera action triggered'),
                          ),
                        );
                      },
                      icon: const Icon(
                        Icons.camera_alt,
                        size: 14,
                        color: Colors.white,
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 20),
          _profileField('User name', _name),
          _profileField('Email', _email),
          _profileField('Password', _password),
          _profileField(
            'Date of Birth',
            _dob,
            suffix: Icons.keyboard_arrow_down,
          ),
          _profileField(
            'Country/Region',
            _country,
            suffix: Icons.keyboard_arrow_down,
          ),
          const SizedBox(height: 20),
          SizedBox(
            height: 52,
            child: ElevatedButton(
              onPressed: () {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Profile updated')),
                );
              },
              style: ElevatedButton.styleFrom(
                backgroundColor: const Color(0xFF2E2F72),
                foregroundColor: Colors.white,
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(8),
                ),
              ),
              child: const Text(
                'Save changes',
                style: TextStyle(fontSize: 22 / 1.2),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _profileField(
    String label,
    TextEditingController controller, {
    IconData? suffix,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: const TextStyle(
              fontSize: 30 / 1.2,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 6),
          TextField(
            controller: controller,
            decoration: InputDecoration(
              filled: true,
              fillColor: const Color(0xFFF1F1F1),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
                borderSide: const BorderSide(color: Color(0xFFD5D5D5)),
              ),
              enabledBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
                borderSide: const BorderSide(color: Color(0xFFD5D5D5)),
              ),
              suffixIcon: suffix != null ? Icon(suffix) : null,
            ),
          ),
        ],
      ),
    );
  }
}

class IotHomePage extends StatefulWidget {
  const IotHomePage({super.key});
  @override
  State<IotHomePage> createState() => _IotHomePageState();
}

class _IotHomePageState extends State<IotHomePage> {
  static const String _roomsPrefsKey = 'iot_rooms_v1';
  static const String _relayRoomName = 'ESP32 Relay Board';
  final ImagePicker _picker = ImagePicker();
  final List<_RoomData> _rooms = [
    _RoomData(
      name: _relayRoomName,
      devices: [
        _IotDeviceData(name: 'Relay 1', isOn: false, relayIndex: 1),
        _IotDeviceData(name: 'Relay 2', isOn: false, relayIndex: 2),
      ],
    ),
    _RoomData(
      name: 'Room 2',
      devices: [_IotDeviceData(name: 'Device 1', isOn: false)],
    ),
    _RoomData(name: 'Room 3', devices: []),
  ];

  @override
  void initState() {
    super.initState();
    _loadRooms();
    IotService.instance.stateNotifier.addListener(_syncRelayDevicesFromService);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _syncRelayDevicesFromService();
    });
  }

  @override
  void dispose() {
    IotService.instance.stateNotifier.removeListener(_syncRelayDevicesFromService);
    super.dispose();
  }

  Future<void> _loadRooms() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_roomsPrefsKey);
    if (raw == null || raw.isEmpty) return;

    try {
      final decoded = jsonDecode(raw);
      if (decoded is! List) return;
      final loaded = decoded
          .whereType<Map<String, dynamic>>()
          .map(_RoomData.fromJson)
          .toList();
      if (!mounted) return;
      setState(() {
        _rooms
          ..clear()
          ..addAll(loaded);
        _ensureRelayRoom();
      });
    } catch (_) {}
  }

  Future<void> _saveRooms() async {
    final prefs = await SharedPreferences.getInstance();
    final data = _rooms.map((room) => room.toJson()).toList();
    await prefs.setString(_roomsPrefsKey, jsonEncode(data));
  }

  void _ensureRelayRoom() {
    final relayRoomIndex = _rooms.indexWhere((room) => room.name == _relayRoomName);
    if (relayRoomIndex == -1) {
      _rooms.insert(
        0,
        _RoomData(
          name: _relayRoomName,
          devices: [
            _IotDeviceData(name: 'Relay 1', isOn: false, relayIndex: 1),
            _IotDeviceData(name: 'Relay 2', isOn: false, relayIndex: 2),
          ],
        ),
      );
      return;
    }

    final relayRoom = _rooms[relayRoomIndex];
    _ensureRelayDevice(relayRoom, 1);
    _ensureRelayDevice(relayRoom, 2);
  }

  void _ensureRelayDevice(_RoomData room, int relayIndex) {
    final existing = room.devices.where((device) => device.relayIndex == relayIndex);
    if (existing.isNotEmpty) {
      return;
    }
    room.devices.insert(
      relayIndex - 1,
      _IotDeviceData(
        name: 'Relay $relayIndex',
        isOn: false,
        relayIndex: relayIndex,
      ),
    );
  }

  void _syncRelayDevicesFromService() {
    if (!mounted) return;

    final serviceState = IotService.instance.state;
    bool changed = false;
    _ensureRelayRoom();

    for (final room in _rooms) {
      for (final device in room.devices) {
        if (device.relayIndex == 1 && device.isOn != serviceState.lightOn) {
          device.isOn = serviceState.lightOn;
          changed = true;
        } else if (device.relayIndex == 2 && device.isOn != serviceState.fanOn) {
          device.isOn = serviceState.fanOn;
          changed = true;
        }
      }
    }

    if (changed) {
      setState(() {});
      unawaited(_saveRooms());
    }
  }

  Future<void> _toggleDevice(_IotDeviceData device, bool value) async {
    setState(() => device.isOn = value);
    if (device.relayIndex == 1) {
      IotService.instance.setLight(value);
    } else if (device.relayIndex == 2) {
      IotService.instance.setFan(value);
    }
    await _saveRooms();
  }

  Future<String?> _pickImagePath() async {
    try {
      final picked = await _picker.pickImage(
        source: ImageSource.gallery,
        imageQuality: 85,
        maxWidth: 1400,
      );
      return picked?.path;
    } on PlatformException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Image picker error: ${e.message ?? e.code}')),
        );
      }
      return null;
    }
  }

  Widget _coverBox({
    required String? path,
    required double size,
    required IconData fallbackIcon,
    BorderRadius? radius,
  }) {
    final borderRadius = radius ?? BorderRadius.circular(12);
    final file = path != null && path.isNotEmpty ? File(path) : null;
    final hasImage = file != null && file.existsSync();
    return ClipRRect(
      borderRadius: borderRadius,
      child: Container(
        width: size,
        height: size,
        color: const Color(0xFFD6D6D6),
        child: hasImage
            ? Image.file(file, fit: BoxFit.cover)
            : Icon(fallbackIcon, color: const Color(0xFFAEAFB1)),
      ),
    );
  }

  Future<void> _updateRoomCover(_RoomData room) async {
    final path = await _pickImagePath();
    if (!mounted || path == null) return;
    setState(() => room.coverPath = path);
    await _saveRooms();
  }

  Future<void> _renameRoom(_RoomData room) async {
    final controller = TextEditingController(text: room.name);
    final newName = await showDialog<String>(
      context: context,
      builder: (dialogContext) {
        return AlertDialog(
          title: const Text('Rename room'),
          content: TextField(
            controller: controller,
            autofocus: true,
            decoration: const InputDecoration(hintText: 'Room name'),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(),
              child: const Text('Cancel'),
            ),
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(controller.text.trim()),
              child: const Text('Save'),
            ),
          ],
        );
      },
    );

    if (!mounted || newName == null || newName.isEmpty || newName == room.name) {
      return;
    }

    setState(() => room.name = newName);
    await _saveRooms();
  }

  Future<void> _deleteRoom(_RoomData room) async {
    final shouldDelete = await showDialog<bool>(
      context: context,
      builder: (dialogContext) {
        return AlertDialog(
          title: const Text('Delete room'),
          content: Text('Delete "${room.name}" and all devices in this room?'),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: const Text('Cancel'),
            ),
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(true),
              child: const Text('Delete', style: TextStyle(color: Colors.red)),
            ),
          ],
        );
      },
    );
    if (shouldDelete == true && mounted) {
      setState(() => _rooms.remove(room));
      await _saveRooms();
    }
  }

  @override
  Widget build(BuildContext context) {
    _ensureRelayRoom();
    final activeDevices = _rooms
        .expand((r) => r.devices)
        .where((device) => device.isOn)
        .toList();
    return Scaffold(
      backgroundColor: const Color(0xFFF2F2F2),
      appBar: AppBar(
        backgroundColor: const Color(0xFFF2F2F2),
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_ios_new, color: Colors.black87),
          onPressed: () => Navigator.of(context).pop(),
        ),
        centerTitle: true,
        title: const Text(
          'Home',
          style: TextStyle(color: Colors.black, fontWeight: FontWeight.w700),
        ),
      ),
      body: Stack(
        children: [
          ListView(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 84),
            children: [
              const Text('Actives', style: TextStyle(fontSize: 36 / 1.2)),
              const SizedBox(height: 8),
              const SizedBox(height: 8),
              SizedBox(
                height: 178,
                child: ListView.separated(
                  scrollDirection: Axis.horizontal,
                  itemCount: activeDevices.length,
                  separatorBuilder: (_, index) => const SizedBox(width: 10),
                  itemBuilder: (context, index) {
                    final device = activeDevices[index];
                    return Container(
                      width: 130,
                      padding: const EdgeInsets.all(8),
                      decoration: BoxDecoration(
                        color: const Color(0xFFE4E1EA),
                        borderRadius: BorderRadius.circular(14),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Container(
                            height: 82,
                            decoration: BoxDecoration(
                              color: const Color(0xFFDAD6E1),
                              borderRadius: BorderRadius.circular(10),
                            ),
                            child: Center(
                              child: _coverBox(
                                path: device.coverPath,
                                size: 70,
                                fallbackIcon: Icons.widgets_outlined,
                                radius: BorderRadius.circular(10),
                              ),
                            ),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            device.name,
                            style: const TextStyle(fontWeight: FontWeight.w600),
                          ),
                          Row(
                            children: [
                              const Text(
                                'Room',
                                style: TextStyle(color: Color(0xFF6E6A74)),
                              ),
                              const Spacer(),
                              Transform.scale(
                                scale: 0.88,
                                child: Switch(
                                  materialTapTargetSize:
                                      MaterialTapTargetSize.shrinkWrap,
                                  value: device.isOn,
                                  activeThumbColor: const Color(0xFF6E4FB2),
                                  onChanged: (v) async {
                                    await _toggleDevice(device, v);
                                  },
                                ),
                              ),
                            ],
                          ),
                        ],
                      ),
                    );
                  },
                ),
              ),
              const SizedBox(height: 16),
              const Text('Rooms', style: TextStyle(fontSize: 36 / 1.2)),
              const SizedBox(height: 8),
              ..._rooms.map(
                (room) => ListTile(
                  contentPadding: const EdgeInsets.symmetric(vertical: 6),
                  leading: _coverBox(
                    path: room.coverPath,
                    size: 56,
                    fallbackIcon: Icons.blur_circular,
                    radius: BorderRadius.circular(8),
                  ),
                  title: Text(
                    room.name,
                    style: const TextStyle(fontSize: 24 / 1.2),
                  ),
                  subtitle: Text('${room.devices.length} Device'),
                  trailing: Wrap(
                    spacing: 2,
                    children: [
                      IconButton(
                        icon: const Icon(Icons.edit_outlined),
                        onPressed: () => _renameRoom(room),
                      ),
                      IconButton(
                        icon: const Icon(Icons.image_outlined),
                        onPressed: () => _updateRoomCover(room),
                      ),
                      IconButton(
                        icon: const Icon(
                          Icons.delete_outline,
                          color: Colors.redAccent,
                        ),
                        onPressed: () => _deleteRoom(room),
                      ),
                    ],
                  ),
                  onTap: () async {
                    await Navigator.of(context).push(
                      MaterialPageRoute<void>(
                        builder: (_) =>
                            _RoomDetailPage(
                              room: room,
                              onChanged: _saveRooms,
                              onToggleDevice: _toggleDevice,
                            ),
                      ),
                    );
                    if (mounted) {
                      setState(() {});
                      await _saveRooms();
                    }
                  },
                ),
              ),
            ],
          ),
          Positioned(
            left: 0,
            right: 0,
            bottom: 18,
            child: Center(
              child: SizedBox(
                height: 52,
                child: ElevatedButton.icon(
                  onPressed: _showCreateRoomDialog,
                  icon: const Icon(Icons.add),
                  label: const Text('CREATE NEW ROOM'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF0F86EA),
                    foregroundColor: Colors.white,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(28),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _showCreateRoomDialog() async {
    final controller = TextEditingController();
    String? coverPath;
    await showDialog<void>(
      context: context,
      builder: (dialogContext) {
        return StatefulBuilder(
          builder: (context, setDialogState) {
            return AlertDialog(
              backgroundColor: const Color(0xFFF1F1F1),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(28),
              ),
              title: const Text(
                'Create new room',
                style: TextStyle(fontWeight: FontWeight.w700),
              ),
              content: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  TextField(
                    controller: controller,
                    decoration: InputDecoration(
                      hintText: 'Room name',
                      filled: true,
                      fillColor: const Color(0xFFE4E4E6),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(28),
                        borderSide: BorderSide.none,
                      ),
                    ),
                  ),
                  const SizedBox(height: 12),
                  _coverBox(
                    path: coverPath,
                    size: 110,
                    fallbackIcon: Icons.home_outlined,
                    radius: BorderRadius.circular(14),
                  ),
                  const SizedBox(height: 8),
                  _dialogButton(
                    text: 'Choose cover image',
                    bg: const Color(0xFFE2E2E4),
                    fg: Colors.black87,
                    onTap: () async {
                      final pickedPath = await _pickImagePath();
                      if (pickedPath == null) return;
                      setDialogState(() => coverPath = pickedPath);
                    },
                  ),
                  const SizedBox(height: 12),
                  _dialogButton(
                    text: 'Create',
                    bg: const Color(0xFF0F86EA),
                    fg: Colors.white,
                    onTap: () async {
                      final name = controller.text.trim();
                      if (name.isNotEmpty) {
                        setState(() {
                          _rooms.add(
                            _RoomData(
                              name: name,
                              devices: [],
                              coverPath: coverPath,
                            ),
                          );
                        });
                        _saveRooms();
                      }
                      Navigator.of(dialogContext).pop();
                    },
                  ),
                  const SizedBox(height: 8),
                  _dialogButton(
                    text: 'Cancel',
                    bg: const Color(0xFFE2E2E4),
                    fg: Colors.red,
                    onTap: () => Navigator.of(dialogContext).pop(),
                  ),
                ],
              ),
            );
          },
        );
      },
    );
  }

  Widget _dialogButton({
    required String text,
    required Color bg,
    required Color fg,
    required VoidCallback onTap,
  }) {
    return SizedBox(
      width: double.infinity,
      height: 48,
      child: ElevatedButton(
        onPressed: onTap,
        style: ElevatedButton.styleFrom(
          backgroundColor: bg,
          foregroundColor: fg,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(28),
          ),
        ),
        child: Text(text, style: const TextStyle(fontSize: 20 / 1.2)),
      ),
    );
  }
}

class _RoomDetailPage extends StatefulWidget {
  const _RoomDetailPage({
    required this.room,
    required this.onChanged,
    required this.onToggleDevice,
  });

  final _RoomData room;
  final Future<void> Function() onChanged;
  final Future<void> Function(_IotDeviceData device, bool value) onToggleDevice;

  @override
  State<_RoomDetailPage> createState() => _RoomDetailPageState();
}

class _RoomDetailPageState extends State<_RoomDetailPage> {
  final ImagePicker _picker = ImagePicker();
  Future<String?> _pickImagePath() async {
    try {
      final picked = await _picker.pickImage(
        source: ImageSource.gallery,
        imageQuality: 85,
        maxWidth: 1400,
      );
      return picked?.path;
    } on PlatformException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Image picker error: ${e.message ?? e.code}')),
        );
      }
      return null;
    }
  }

  Widget _coverBox({
    required String? path,
    required double size,
    required IconData fallbackIcon,
    BorderRadius? radius,
  }) {
    final borderRadius = radius ?? BorderRadius.circular(12);
    final file = path != null && path.isNotEmpty ? File(path) : null;
    final hasImage = file != null && file.existsSync();
    return ClipRRect(
      borderRadius: borderRadius,
      child: Container(
        width: size,
        height: size,
        color: const Color(0xFFE4E1EA),
        child: hasImage
            ? Image.file(file, fit: BoxFit.cover)
            : Icon(
                fallbackIcon,
                size: size * 0.45,
                color: const Color(0xFFB6B1BE),
              ),
      ),
    );
  }

  Future<void> _updateDeviceCover(_IotDeviceData device) async {
    final path = await _pickImagePath();
    if (!mounted || path == null) return;
    setState(() => device.coverPath = path);
    await widget.onChanged();
  }

  Future<void> _renameDevice(_IotDeviceData device) async {
    final controller = TextEditingController(text: device.name);
    final newName = await showDialog<String>(
      context: context,
      builder: (dialogContext) {
        return AlertDialog(
          title: const Text('Rename device'),
          content: TextField(
            controller: controller,
            autofocus: true,
            decoration: const InputDecoration(hintText: 'Device name'),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(),
              child: const Text('Cancel'),
            ),
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(controller.text.trim()),
              child: const Text('Save'),
            ),
          ],
        );
      },
    );

    if (!mounted || newName == null || newName.isEmpty || newName == device.name) {
      return;
    }

    setState(() => device.name = newName);
    await widget.onChanged();
  }

  Future<void> _deleteDevice(_IotDeviceData device) async {
    final shouldDelete = await showDialog<bool>(
      context: context,
      builder: (dialogContext) {
        return AlertDialog(
          title: const Text('Delete device'),
          content: Text('Delete "${device.name}"?'),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: const Text('Cancel'),
            ),
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(true),
              child: const Text('Delete', style: TextStyle(color: Colors.red)),
            ),
          ],
        );
      },
    );
    if (shouldDelete == true && mounted) {
      setState(() => widget.room.devices.remove(device));
      await widget.onChanged();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF2F2F2),
      appBar: AppBar(
        backgroundColor: const Color(0xFFF2F2F2),
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_ios_new, color: Colors.black87),
          onPressed: () => Navigator.of(context).pop(),
        ),
        centerTitle: true,
        title: Text(
          widget.room.name,
          style: const TextStyle(
            color: Colors.black,
            fontWeight: FontWeight.w700,
          ),
        ),
      ),
      body: Stack(
        children: [
          ListView.builder(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 84),
            itemCount: widget.room.devices.length,
            itemBuilder: (context, index) {
              final device = widget.room.devices[index];
              return Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Row(
                  children: [
                    _coverBox(
                      path: device.coverPath,
                      size: 96,
                      fallbackIcon: Icons.widgets_outlined,
                      radius: BorderRadius.circular(14),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            device.name,
                            style: const TextStyle(fontSize: 30 / 1.2),
                          ),
                          const SizedBox(height: 8),
                          Wrap(
                            spacing: 2,
                            children: [
                              TextButton.icon(
                                onPressed: () => _renameDevice(device),
                                icon: const Icon(Icons.edit_outlined, size: 16),
                                label: const Text('Rename'),
                              ),
                              TextButton.icon(
                                onPressed: () => _updateDeviceCover(device),
                                icon: const Icon(
                                  Icons.image_outlined,
                                  size: 16,
                                ),
                                label: const Text('Cover'),
                              ),
                              TextButton.icon(
                                onPressed: () => _deleteDevice(device),
                                icon: const Icon(
                                  Icons.delete_outline,
                                  size: 16,
                                  color: Colors.redAccent,
                                ),
                                label: const Text(
                                  'Delete',
                                  style: TextStyle(color: Colors.redAccent),
                                ),
                              ),
                            ],
                          ),
                        ],
                      ),
                    ),
                    Switch(
                      value: device.isOn,
                      activeThumbColor: const Color(0xFF6E4FB2),
                      onChanged: (v) async {
                        setState(() => device.isOn = v);
                        await widget.onToggleDevice(device, v);
                      },
                    ),
                  ],
                ),
              );
            },
          ),
          Positioned(
            left: 0,
            right: 0,
            bottom: 18,
            child: Center(
              child: SizedBox(
                height: 52,
                child: ElevatedButton.icon(
                  onPressed: _showCreateDeviceDialog,
                  icon: const Icon(Icons.add),
                  label: const Text('CREATE NEW DEVICE'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF0F86EA),
                    foregroundColor: Colors.white,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(28),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _showCreateDeviceDialog() async {
    final controller = TextEditingController();
    String? coverPath;
    await showDialog<void>(
      context: context,
      builder: (dialogContext) {
        return StatefulBuilder(
          builder: (context, setDialogState) {
            return AlertDialog(
              backgroundColor: const Color(0xFFF1F1F1),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(28),
              ),
              title: const Text(
                'Create new device',
                style: TextStyle(fontWeight: FontWeight.w700),
              ),
              content: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  TextField(
                    controller: controller,
                    decoration: InputDecoration(
                      hintText: 'Device name',
                      filled: true,
                      fillColor: const Color(0xFFE4E4E6),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(28),
                        borderSide: BorderSide.none,
                      ),
                    ),
                  ),
                  const SizedBox(height: 12),
                  _coverBox(
                    path: coverPath,
                    size: 110,
                    fallbackIcon: Icons.widgets_outlined,
                    radius: BorderRadius.circular(14),
                  ),
                  const SizedBox(height: 8),
                  _dialogButton(
                    text: 'Choose cover image',
                    bg: const Color(0xFFE2E2E4),
                    fg: Colors.black87,
                    onTap: () async {
                      final pickedPath = await _pickImagePath();
                      if (pickedPath == null) return;
                      setDialogState(() => coverPath = pickedPath);
                    },
                  ),
                  const SizedBox(height: 12),
                  _dialogButton(
                    text: 'Create',
                    bg: const Color(0xFF0F86EA),
                    fg: Colors.white,
                    onTap: () async {
                      final name = controller.text.trim();
                      if (name.isNotEmpty) {
                        setState(() {
                          widget.room.devices.add(
                            _IotDeviceData(
                              name: name,
                              isOn: false,
                              coverPath: coverPath,
                            ),
                          );
                        });
                        widget.onChanged();
                      }
                      Navigator.of(dialogContext).pop();
                    },
                  ),
                  const SizedBox(height: 8),
                  _dialogButton(
                    text: 'Cancel',
                    bg: const Color(0xFFE2E2E4),
                    fg: Colors.red,
                    onTap: () => Navigator.of(dialogContext).pop(),
                  ),
                ],
              ),
            );
          },
        );
      },
    );
  }

  Widget _dialogButton({
    required String text,
    required Color bg,
    required Color fg,
    required VoidCallback onTap,
  }) {
    return SizedBox(
      width: double.infinity,
      height: 48,
      child: ElevatedButton(
        onPressed: onTap,
        style: ElevatedButton.styleFrom(
          backgroundColor: bg,
          foregroundColor: fg,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(28),
          ),
        ),
        child: Text(text, style: const TextStyle(fontSize: 20 / 1.2)),
      ),
    );
  }
}

class _RoomData {
  _RoomData({required this.name, required this.devices, this.coverPath});

  factory _RoomData.fromJson(Map<String, dynamic> json) {
    final devicesJson = json['devices'];
    final devices = devicesJson is List
        ? devicesJson
              .whereType<Map<String, dynamic>>()
              .map(_IotDeviceData.fromJson)
              .toList()
        : <_IotDeviceData>[];
    return _RoomData(
      name: json['name']?.toString() ?? 'Room',
      devices: devices,
      coverPath: json['coverPath']?.toString(),
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'name': name,
      'coverPath': coverPath,
      'devices': devices.map((e) => e.toJson()).toList(),
    };
  }

  String name;
  final List<_IotDeviceData> devices;
  String? coverPath;
}

class _IotDeviceData {
  _IotDeviceData({
    required this.name,
    required this.isOn,
    this.coverPath,
    this.relayIndex,
  });

  factory _IotDeviceData.fromJson(Map<String, dynamic> json) {
    return _IotDeviceData(
      name: json['name']?.toString() ?? 'Device',
      isOn: json['isOn'] == true,
      coverPath: json['coverPath']?.toString(),
      relayIndex: (json['relayIndex'] as num?)?.toInt(),
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'name': name,
      'isOn': isOn,
      'coverPath': coverPath,
      'relayIndex': relayIndex,
    };
  }

  String name;
  bool isOn;
  String? coverPath;
  int? relayIndex;
}
