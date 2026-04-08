import 'dart:async';

import 'package:flutter/material.dart';
import 'package:petbox/config/app_config.dart';
import 'package:petbox/app/auth_gate.dart';

class SplashPage extends StatefulWidget {
  const SplashPage({super.key});

  @override
  State<SplashPage> createState() => _SplashPageState();
}

class _SplashPageState extends State<SplashPage> {
  Timer? _navigateTimer;

  @override
  void initState() {
    super.initState();
    _navigateTimer = Timer(const Duration(seconds: 2), _goToAuthGate);
  }

  @override
  void dispose() {
    // Prevent a delayed callback from running after widget disposal.
    _navigateTimer?.cancel();
    super.dispose();
  }

  void _goToAuthGate() {
    if (!mounted) return;
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(builder: (_) => const AuthGate()),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Semantics(
        label: 'Petbox splash screen',
        image: true,
        child: SizedBox.expand(
          child: Image(
            image: AssetImage(AppAssets.splashImage),
            fit: BoxFit.cover,
          ),
        ),
      ),
    );
  }
}

