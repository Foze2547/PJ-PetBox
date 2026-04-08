import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:mqtt_client/mqtt_client.dart';
import 'package:mqtt_client/mqtt_server_client.dart';
import 'package:petbox/config/app_config.dart';
import 'package:petbox/services/iot_backend_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

class DeviceState {
  const DeviceState({
    required this.lightOn,
    required this.fanOn,
    required this.pumpOn,
    required this.autoMode,
    required this.robotSpeed,
  });

  final bool lightOn;
  final bool fanOn;
  final bool pumpOn;
  final bool autoMode;
  final int robotSpeed;

  DeviceState copyWith({
    bool? lightOn,
    bool? fanOn,
    bool? pumpOn,
    bool? autoMode,
    int? robotSpeed,
  }) {
    return DeviceState(
      lightOn: lightOn ?? this.lightOn,
      fanOn: fanOn ?? this.fanOn,
      pumpOn: pumpOn ?? this.pumpOn,
      autoMode: autoMode ?? this.autoMode,
      robotSpeed: robotSpeed ?? this.robotSpeed,
    );
  }
}

class RobotControl {
  RobotControl({this.deviceId = IotBackendConfig.defaultDeviceId});

  final String deviceId;

  Future<bool> sendCommand(String command, {double? readSeconds}) async {
    final normalized = command.trim().toLowerCase();
    final robotCmd = _normalizeRobotCommand(normalized);
    if (robotCmd == null) {
      debugPrint('Unsupported robot command: $command');
      return false;
    }

    final mqttSent = await IotService.instance.publishRobotCommand(robotCmd);
    if (mqttSent) {
      return true;
    }

    // Fallback for deployments that still use backend device control.
    final mapped = _mapFallbackControl(robotCmd);
    if (mapped == null) {
      return false;
    }
    final result = await IotBackendService.instance.controlDevice(
      deviceId: deviceId,
      channel: mapped.channel,
      value: mapped.value,
    );
    return result.ok;
  }

  Future<bool> sendConfigSpeed(int speed) async {
    final result = await IotBackendService.instance.controlDevice(
      deviceId: deviceId,
      channel: 'config.speed',
      value: speed.clamp(0, 255),
    );
    return result.ok;
  }

  Future<bool> sendConfigAngle(int angle) async {
    final result = await IotBackendService.instance.controlDevice(
      deviceId: deviceId,
      channel: 'config.angle',
      value: angle.clamp(0, 90),
    );
    return result.ok;
  }

  String? _normalizeRobotCommand(String command) {
    if (command == 'forward' || command == 'backward') {
      return command;
    }
    if (command == 'left' || command == 'right' || command == 'reset') {
      return command;
    }
    if (command == 'stop' || command == 'soft_stop' || command == 'hard_stop') {
      return command == 'stop' ? 'soft_stop' : command;
    }
    return null;
  }

  _MappedCommand? _mapFallbackControl(String command) {
    if (command == 'forward') {
      return const _MappedCommand(channel: 'motor', value: 'forward');
    }
    if (command == 'backward') {
      return const _MappedCommand(channel: 'motor', value: 'backward');
    }
    if (command == 'stop' || command == 'soft_stop' || command == 'hard_stop') {
      return _MappedCommand(
        channel: 'motor',
        value: command == 'stop' ? 'soft_stop' : command,
      );
    }
    if (command == 'left' || command == 'right' || command == 'reset') {
      return _MappedCommand(channel: 'steer', value: command);
    }
    return null;
  }
}

class _MappedCommand {
  const _MappedCommand({required this.channel, required this.value});

  final String channel;
  final Object value;
}

class IotService {
  IotService._();

  static final IotService instance = IotService._();
  static const _prefsKey = 'iot_device_state_v1';
  static const _mqttHost = '058acb9373964025a71851d4a0030e8a.s1.eu.hivemq.cloud';
  static const _mqttPort = 8883;
  static const _mqttUser = 'Fozexe';
  static const _mqttPass = 'MySecurePassword123!';
  static const _topicRelay1Set = 'mechcode/relay1/set';
  static const _topicRelay2Set = 'mechcode/relay2/set';
  static const _topicRelayAllSet = 'mechcode/relay/all/set';
  static const _topicRobotMotorSet = 'robot/control/motor';
  static const _topicRobotSteerSet = 'robot/control/steer';
  static const _topicRelay1State = 'mechcode/relay1/state';
  static const _topicRelay2State = 'mechcode/relay2/state';
  static const _topicStatus = 'mechcode/esp32/status';

  bool _initialized = false;
  MqttServerClient? _mqttClient;
  StreamSubscription<List<MqttReceivedMessage<MqttMessage>>>? _updatesSub;
  Future<void>? _connectFuture;

  final ValueNotifier<DeviceState> stateNotifier = ValueNotifier<DeviceState>(
    const DeviceState(
      lightOn: false,
      fanOn: false,
      pumpOn: false,
      autoMode: true,
      robotSpeed: 40,
    ),
  );

  DeviceState get state => stateNotifier.value;

  Future<void> init() async {
    if (_initialized) return;
    _initialized = true;

    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_prefsKey);
    if (raw != null && raw.isNotEmpty) {
      try {
        final data = jsonDecode(raw);
        if (data is Map<String, dynamic>) {
          stateNotifier.value = DeviceState(
            lightOn: data['lightOn'] == true,
            fanOn: data['fanOn'] == true,
            pumpOn: data['pumpOn'] == true,
            autoMode: data['autoMode'] != false,
            robotSpeed: ((data['robotSpeed'] as num?)?.toInt() ?? 40).clamp(
              0,
              100,
            ),
          );
        }
      } catch (_) {}
    }

    stateNotifier.addListener(_persistState);
    unawaited(_ensureMqttConnected());
  }

  Future<void> _persistState() async {
    final prefs = await SharedPreferences.getInstance();
    final data = {
      'lightOn': state.lightOn,
      'fanOn': state.fanOn,
      'pumpOn': state.pumpOn,
      'autoMode': state.autoMode,
      'robotSpeed': state.robotSpeed,
    };
    await prefs.setString(_prefsKey, jsonEncode(data));
  }

  void setLight(bool value) {
    stateNotifier.value = state.copyWith(lightOn: value);
    unawaited(_publishRelayCommand(_topicRelay1Set, value));
  }

  void setFan(bool value) {
    stateNotifier.value = state.copyWith(fanOn: value);
    unawaited(_publishRelayCommand(_topicRelay2Set, value));
  }

  void setAllRelays(bool value) {
    stateNotifier.value = state.copyWith(lightOn: value, fanOn: value);
    unawaited(_publishRelayCommand(_topicRelayAllSet, value));
  }

  Future<bool> publishRobotCommand(String command) async {
    final normalized = command.trim().toLowerCase();
    final topic = _robotCommandTopic(normalized);
    if (topic == null) {
      return false;
    }

    await _ensureMqttConnected();
    final client = _mqttClient;
    if (client == null ||
        client.connectionStatus?.state != MqttConnectionState.connected) {
      return false;
    }

    final builder = MqttClientPayloadBuilder()..addString(normalized);
    client.publishMessage(
      topic,
      MqttQos.atLeastOnce,
      builder.payload!,
      retain: false,
    );
    return true;
  }

  String? _robotCommandTopic(String command) {
    if (command == 'left' || command == 'right' || command == 'reset') {
      return _topicRobotSteerSet;
    }
    if (command == 'forward' ||
        command == 'backward' ||
        command == 'soft_stop' ||
        command == 'hard_stop') {
      return _topicRobotMotorSet;
    }
    return null;
  }

  void setPump(bool value) {
    stateNotifier.value = state.copyWith(pumpOn: value);
    _sendControl('pump', value ? 1 : 0);
  }

  void setAutoMode(bool value) {
    stateNotifier.value = state.copyWith(autoMode: value);
    _sendControl('autoMode', value ? 1 : 0);
  }

  void setRobotSpeed(int value) {
    final speed = value.clamp(0, 100);
    stateNotifier.value = state.copyWith(robotSpeed: speed);
    _sendControl('speed', speed);
  }

  void _sendControl(String channel, Object value) {
    IotBackendService.instance
        .controlDevice(
          deviceId: IotBackendConfig.defaultDeviceId,
          channel: channel,
          value: value,
        )
        .then((result) {
          if (!result.ok) {
            debugPrint(
              'Control failed channel=$channel requestId=${result.requestId} status=${result.statusCode} message=${result.message}',
            );
          }
        });
  }

  String processAiCommand(String input) {
    final command = input.toLowerCase().trim();
    if (command.isEmpty) return 'Please type a command.';

    if (_contains(command, [
      'เปิดไฟ 2 ดวง',
      'เปิดไฟสองดวง',
      'เปิดไฟทั้งสอง',
      'เปิดไฟทั้งหมด',
      'turn on both lights',
      'lights on all',
    ])) {
      setAllRelays(true);
      return 'Both lights turned on.';
    }
    if (_contains(command, [
      'ปิดไฟ 2 ดวง',
      'ปิดไฟสองดวง',
      'ปิดไฟทั้งสอง',
      'ปิดไฟทั้งหมด',
      'turn off both lights',
      'lights off all',
    ])) {
      setAllRelays(false);
      return 'Both lights turned off.';
    }

    if (_contains(command, ['light on', 'turn on light', 'open light'])) {
      setLight(true);
      return 'Light turned on.';
    }
    if (_contains(command, ['light off', 'turn off light', 'close light'])) {
      setLight(false);
      return 'Light turned off.';
    }
    if (_contains(command, ['fan on', 'turn on fan'])) {
      setFan(true);
      return 'Fan turned on.';
    }
    if (_contains(command, ['fan off', 'turn off fan'])) {
      setFan(false);
      return 'Fan turned off.';
    }
    if (_contains(command, ['pump on', 'turn on pump'])) {
      setPump(true);
      return 'Pump turned on.';
    }
    if (_contains(command, ['pump off', 'turn off pump'])) {
      setPump(false);
      return 'Pump turned off.';
    }
    if (_contains(command, ['auto mode on', 'enable auto mode'])) {
      setAutoMode(true);
      return 'Auto mode enabled.';
    }
    if (_contains(command, ['auto mode off', 'disable auto mode'])) {
      setAutoMode(false);
      return 'Auto mode disabled.';
    }

    final speedMatch = RegExp(
      r'(speed|set speed)\s*(to)?\s*(\d{1,3})',
    ).firstMatch(command);
    if (speedMatch != null) {
      final speed = int.tryParse(speedMatch.group(3) ?? '');
      if (speed != null) {
        setRobotSpeed(speed);
        return 'Robot speed set to ${state.robotSpeed}%.';
      }
    }

    if (_contains(command, ['status', 'device status'])) {
      return 'Status: light=${state.lightOn ? 'on' : 'off'}, '
          'fan=${state.fanOn ? 'on' : 'off'}, '
          'pump=${state.pumpOn ? 'on' : 'off'}, '
          'auto=${state.autoMode ? 'on' : 'off'}, '
          'speed=${state.robotSpeed}%.';
    }

    return 'Unknown command. Try: "light on", "fan off", "set speed 70", "status".';
  }

  bool _contains(String source, List<String> patterns) {
    return patterns.any(source.contains);
  }

  Future<void> reconnectMqtt() async {
    _mqttClient?.disconnect();
    _mqttClient = null;
    await _ensureMqttConnected();
  }

  Future<void> _ensureMqttConnected() {
    final pending = _connectFuture;
    if (pending != null) {
      return pending;
    }

    final future = _connectMqtt();
    _connectFuture = future;
    return future.whenComplete(() {
      _connectFuture = null;
    });
  }

  Future<void> _connectMqtt() async {
    if (kIsWeb) {
      debugPrint('MQTT direct control is not supported on Flutter Web.');
      return;
    }

    await _updatesSub?.cancel();
    _updatesSub = null;

    final client = MqttServerClient.withPort(
      _mqttHost,
      'petbox_${DateTime.now().millisecondsSinceEpoch}',
      _mqttPort,
    );
    client.secure = true;
    client.securityContext = SecurityContext.defaultContext;
    client.onBadCertificate = (Object _) => true;
    client.keepAlivePeriod = 20;
    client.autoReconnect = true;
    client.resubscribeOnAutoReconnect = true;
    client.logging(on: false);
    client.connectionMessage = MqttConnectMessage()
        .authenticateAs(_mqttUser, _mqttPass)
        .withClientIdentifier(client.clientIdentifier)
        .startClean();

    client.onConnected = () {
      _subscribeTopics(client);
    };
    client.onAutoReconnected = () {
      _subscribeTopics(client);
    };

    try {
      await client.connect();
      if (client.connectionStatus?.state != MqttConnectionState.connected) {
        debugPrint('MQTT connect failed: ${client.connectionStatus?.state}');
        client.disconnect();
        return;
      }

      _mqttClient = client;
      _subscribeTopics(client);
      _updatesSub = client.updates?.listen(_handleMqttMessages);
    } catch (e) {
      debugPrint('MQTT connect error: $e');
      client.disconnect();
    }
  }

  void _subscribeTopics(MqttServerClient client) {
    client.subscribe(_topicRelay1State, MqttQos.atLeastOnce);
    client.subscribe(_topicRelay2State, MqttQos.atLeastOnce);
    client.subscribe(_topicStatus, MqttQos.atLeastOnce);
  }

  void _handleMqttMessages(List<MqttReceivedMessage<MqttMessage>> events) {
    for (final event in events) {
      final payload = event.payload;
      if (payload is! MqttPublishMessage) {
        continue;
      }

      final message = MqttPublishPayload.bytesToStringAsString(
        payload.payload.message,
      ).trim();

      if (event.topic == _topicRelay1State) {
        final value = _parseOnOff(message);
        if (value != null) {
          stateNotifier.value = state.copyWith(lightOn: value);
        }
      } else if (event.topic == _topicRelay2State) {
        final value = _parseOnOff(message);
        if (value != null) {
          stateNotifier.value = state.copyWith(fanOn: value);
        }
      } else if (event.topic == _topicStatus) {
        debugPrint('ESP32 status: $message');
      }
    }
  }

  bool? _parseOnOff(String payload) {
    final normalized = payload.trim().toUpperCase();
    if (normalized == 'ON' || normalized == '1' || normalized == 'TRUE') {
      return true;
    }
    if (normalized == 'OFF' || normalized == '0' || normalized == 'FALSE') {
      return false;
    }
    return null;
  }

  Future<void> _publishRelayCommand(String topic, bool value) async {
    await _ensureMqttConnected();
    final client = _mqttClient;
    if (client == null ||
        client.connectionStatus?.state != MqttConnectionState.connected) {
      return;
    }

    final builder = MqttClientPayloadBuilder()..addString(value ? 'ON' : 'OFF');
    client.publishMessage(
      topic,
      MqttQos.atLeastOnce,
      builder.payload!,
      retain: false,
    );
  }
}
