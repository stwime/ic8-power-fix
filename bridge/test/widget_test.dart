import 'package:flutter_test/flutter_test.dart';

import 'package:ic8_bridge/main.dart';

void main() {
  testWidgets('renders home page', (WidgetTester tester) async {
    await tester.pumpWidget(const App());
    expect(find.text('IC8 Bridge'), findsOneWidget);
  });
}
