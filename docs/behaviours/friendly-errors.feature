# Verified against: frontend/src/api/errors.ts, frontend/src/i18n/translations/ar.ts,
# frontend/src/pages/SigninPage.tsx, backend/rest.py (tests/test_rest.py)
Feature: Friendly Arabic errors in the web page
  The page never shows a status code, an English title or detail from the server, or exception text.
  The status stays on ApiError for code to react to.

  Scenario Outline: A failure becomes an Arabic message
    When a request fails with <failure>
    Then the page shows the message for "<key>"

    Examples:
      | failure               | key                | message                                                                |
      | no connection         | error.network      | تعذّر الاتصال بالخادم. تحقّق من اتصالك بالإنترنت ثم حاول مرة أخرى.    |
      | status 400 or 422     | error.invalid      | البيانات المدخلة غير صحيحة. راجعها وحاول مرة أخرى.                      |
      | status 401            | error.unauthorized | انتهت جلستك. يرجى تسجيل الدخول من جديد.                                |
      | status 403            | error.forbidden    | ليس لديك صلاحية للقيام بهذا الإجراء.                                   |
      | status 404            | error.notFound     | لم نجد ما طلبته. قد يكون قد حُذف أو تغيّر.                              |
      | status 409            | error.conflict     | هذا الطلب يتعارض مع بيانات موجودة، مثل اسم مستخدم محجوز. جرّب قيمة أخرى. |
      | status 429            | error.tooMany      | محاولات كثيرة في وقت قصير. انتظر قليلاً ثم حاول مرة أخرى.              |
      | status 503            | error.busy         | الخادم مشغول الآن بسبب كثرة الطلبات. انتظر لحظات ثم حاول مرة أخرى.     |
      | any other status      | error.generic      | حدث خطأ ما. حاول مرة أخرى بعد قليل.                                    |

  @manual
  Scenario: A wrong password on sign-in has its own message
    When the sign-in call returns 401
    Then the page shows "error.badCredentials" instead of "error.unauthorized"

  Scenario: Sign-up checks are Arabic too
    Then the keys error.usernameShort, error.passwordShort and error.passwordMismatch exist in Arabic

  Scenario: The server's own errors are problem+json
    When any API route fails
    Then the content type is application/problem+json
    And headers set on the exception (for example Retry-After, WWW-Authenticate) are kept
    And a validation failure has detail "Request validation failed" and an errors list with a field name
