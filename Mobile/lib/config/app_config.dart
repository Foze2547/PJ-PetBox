import 'package:flutter/material.dart';

class AppColors {
  static const primaryPurple = Color(0xFF9E76B4);
  static const primaryIndigo = Color(0xFF6868AC);
  static const primaryGreen = Color(0xFF286428);
  static const primaryOrange = Color(0xFFFAAC1E);
  static const primaryBlue = Color(0xFF0089ED);
}

class AppAssets {
  static const splashImage = 'lib/image/Splash Screen .png';
  static const logoImage = 'lib/image/logo.png';
  static const googleIcon = 'lib/image/google.png';
}

class AuthConfig {
  static const googleScopes = <String>['email'];
}

class OpenAiConfig {
  // Set your OpenAI API key here.
  static const apiKey =
      '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';

  // Optional: change model if needed.
  static const model = 'gpt-4';

  // Optional: custom compatible endpoint.
  static const baseUrl = 'https://api.openai.com/v1';
}

class AiServerConfig {
  static const defaultHost = String.fromEnvironment(
    'AI_SERVER_HOST',
    defaultValue: '100.110.201.13',
  );
  static const port = 8000;
}

class IotBackendConfig {
  static const defaultBaseUrl = String.fromEnvironment(
    'IOT_BACKEND_URL',
    defaultValue: 'http://10.0.2.2:3000',
  );
  static const apiPrefix = '/api';
  static const defaultDeviceId = String.fromEnvironment(
    'IOT_DEVICE_ID',
    defaultValue: 'plug001',
  );
  static const requestTimeout = Duration(seconds: 8);
}
