import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:petbox/config/app_config.dart';
import 'package:shared_preferences/shared_preferences.dart';

class DeviceControlResult {
  const DeviceControlResult({
    required this.ok,
    required this.requestId,
    this.message,
    this.statusCode,
  });

  final bool ok;
  final String requestId;
  final String? message;
  final int? statusCode;
}

class IotBackendService {
  IotBackendService._();

  static final IotBackendService instance = IotBackendService._();
  static const _prefsBaseUrlKey = 'iot_backend_base_url_v1';
  static const _prefsTokenKey = 'iot_backend_token_v1';

  Future<String> getBaseUrl() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString(_prefsBaseUrlKey)?.trim() ?? '';
    if (saved.isNotEmpty) {
      return saved;
    }
    return IotBackendConfig.defaultBaseUrl;
  }

  Future<void> setBaseUrl(String baseUrl) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsBaseUrlKey, baseUrl.trim());
  }

  Future<String> getBearerToken() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_prefsTokenKey)?.trim() ?? '';
  }

  Future<void> setBearerToken(String token) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsTokenKey, token.trim());
  }

  Future<DeviceControlResult> controlDevice({
    required String deviceId,
    required String channel,
    required Object value,
    String source = 'mobile-app',
    String? userId,
  }) async {
    final requestId = _buildRequestId();
    final token = await getBearerToken();
    final baseUrl = await getBaseUrl();

    final uri = Uri.parse(
      '$baseUrl${IotBackendConfig.apiPrefix}/devices/$deviceId/control',
    );

    final body = <String, dynamic>{
      'requestId': requestId,
      'channel': channel,
      'value': value,
      'source': source,
      'ts': DateTime.now().millisecondsSinceEpoch ~/ 1000,
      if (userId != null && userId.isNotEmpty) 'userId': userId,
    };

    final headers = <String, String>{
      'Content-Type': 'application/json',
      if (token.isNotEmpty) 'Authorization': 'Bearer $token',
    };

    try {
      final res = await http
          .post(uri, headers: headers, body: jsonEncode(body))
          .timeout(IotBackendConfig.requestTimeout);

      if (res.statusCode >= 200 && res.statusCode < 300) {
        return DeviceControlResult(
          ok: true,
          requestId: requestId,
          statusCode: res.statusCode,
        );
      }

      return DeviceControlResult(
        ok: false,
        requestId: requestId,
        statusCode: res.statusCode,
        message: res.body,
      );
    } catch (e) {
      debugPrint('Backend control error: $e');
      return DeviceControlResult(
        ok: false,
        requestId: requestId,
        message: e.toString(),
      );
    }
  }

  String _buildRequestId() {
    final now = DateTime.now().toUtc();
    final stamp = now.toIso8601String().replaceAll(RegExp(r'[-:.TZ]'), '');
    return 'req-$stamp';
  }
}
