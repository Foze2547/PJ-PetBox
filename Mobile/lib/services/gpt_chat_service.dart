import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:petbox/config/app_config.dart';

class GptChatMessage {
  const GptChatMessage({required this.role, required this.content});

  final String role;
  final String content;

  Map<String, String> toJson() {
    return {
      'role': role,
      'content': content,
    };
  }
}

class GptChatService {
  GptChatService._();

  static final GptChatService instance = GptChatService._();

  static const String _apiKeyFromEnv = String.fromEnvironment('OPENAI_API_KEY');
  static const String _modelFromEnv = String.fromEnvironment('OPENAI_MODEL');
  static const String _baseUrlFromEnv = String.fromEnvironment('OPENAI_BASE_URL');

  Future<String> sendMessage({
    required String message,
    List<GptChatMessage> history = const [],
  }) async {
    final text = message.trim();
    final apiKey = OpenAiConfig.apiKey.trim().isNotEmpty
        ? OpenAiConfig.apiKey.trim()
        : _apiKeyFromEnv;
    final model = _modelFromEnv.isNotEmpty ? _modelFromEnv : OpenAiConfig.model;
    final baseUrl = _baseUrlFromEnv.isNotEmpty
        ? _baseUrlFromEnv
        : OpenAiConfig.baseUrl;

    if (text.isEmpty) {
      return 'Please type a message.';
    }
    if (apiKey.isEmpty) {
      throw Exception(
        'Missing API key. Set OpenAiConfig.apiKey in lib/config/app_config.dart',
      );
    }

    final uri = Uri.parse('$baseUrl/chat/completions');
    final messages = <Map<String, String>>[
      const {
        'role': 'system',
        'content':
            'You are a helpful, concise chatbot. Reply in the user language when possible.',
      },
      ...history.map((m) => m.toJson()),
      {
        'role': 'user',
        'content': text,
      },
    ];

    final response = await http
        .post(
          uri,
          headers: {
            'Authorization': 'Bearer $apiKey',
            'Content-Type': 'application/json',
          },
          body: jsonEncode({
            'model': model,
            'messages': messages,
            'temperature': 0.7,
          }),
        )
        .timeout(const Duration(seconds: 30));

    final data = jsonDecode(response.body);

    if (response.statusCode < 200 || response.statusCode >= 300) {
      final message = data is Map<String, dynamic>
          ? (data['error']?['message']?.toString() ??
              'OpenAI request failed (${response.statusCode})')
          : 'OpenAI request failed (${response.statusCode})';
      throw Exception(message);
    }

    if (data is! Map<String, dynamic>) {
      throw Exception('Invalid OpenAI response format');
    }

    final choices = data['choices'];
    if (choices is List && choices.isNotEmpty) {
      final first = choices.first;
      if (first is Map<String, dynamic>) {
        final content = first['message']?['content']?.toString();
        if (content != null && content.trim().isNotEmpty) {
          return content.trim();
        }
      }
    }

    throw Exception('No response text from OpenAI');
  }
}
