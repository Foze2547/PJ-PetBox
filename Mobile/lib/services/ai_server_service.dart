import 'package:petbox/config/app_config.dart';
import 'package:shared_preferences/shared_preferences.dart';

class AiServerService {
  AiServerService._();

  static final AiServerService instance = AiServerService._();
  static const _prefsKey = 'camera_server_ip_v1';

  Future<String> getHost() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString(_prefsKey)?.trim() ?? '';
    if (saved.isNotEmpty) {
      return saved;
    }
    return AiServerConfig.defaultHost.trim();
  }

  Future<void> setHost(String host) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsKey, host.trim());
  }

  Future<String> getLiveWebSocketUrl() async {
    final host = await getHost();
    if (host.isEmpty) {
      return '';
    }
    return 'ws://$host:${AiServerConfig.port}/ws/live';
  }
}
