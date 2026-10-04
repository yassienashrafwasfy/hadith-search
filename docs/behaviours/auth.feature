# Verified against: backend/routers/auth.py, backend/tokens.py, tests/test_routers.py,
# tests/test_tokens.py, nginx/default.conf, tools/test-nginx.sh
Feature: Sign up, sign in and tokens
  Auth is a stateless signed bearer token (HS256 JWT). No session table, so a token
  cannot be revoked before it expires.

  Scenario: Sign up creates an annotator, assigns queries and returns a token
    When a client sends POST /api/v1/annotators with username "alice" and password "secret123"
    Then the status is 201
    And the Location header is /api/v1/annotators/<id>
    And the body has token_type "Bearer" and expires_in 3600
    And the annotator has 2 assigned queries

  Scenario Outline: Sign up checks the input
    When a client signs up with username "<username>" and password "<password>"
    Then the status is 422
    And the errors name the field "<field>"

    Examples:
      | username | password  | field    |
      | ab       | secret123 | username |
      | alice    | 123       | password |

  Scenario: A taken username is refused
    Given "alice" is already signed up
    When a client signs up as "alice" again
    Then the status is 409 and the title is "Conflict"

  Scenario: A query is given to at most 3 annotators
    Given 3 annotators have signed up
    When a 4th signs up
    Then the 4th still gets 2 queries, and none of them is one already held by 3 people

  Scenario: Sign in exchanges credentials for a token
    Given "alice" is already signed up
    When a client sends POST /api/v1/tokens with the right username and password
    Then the status is 201 and the body holds an access_token
    When the client sends GET /api/v1/annotators/me with that token as a Bearer header
    Then it receives the annotator's profile

  Scenario: Wrong password and unknown user look the same
    Given "alice" is already signed up
    When a client signs in with an unknown username
    And another signs in with a known username and a wrong password
    Then both get the same status and the same body

  @manual
  Scenario: The web page shows a friendly sign-in message
    When the sign-in call returns 401
    Then the page shows "اسم المستخدم أو كلمة المرور غير صحيحة. حاول مرة أخرى."

  Scenario Outline: Bad credentials headers or tokens are refused
    When a client calls GET /api/v1/annotators/me with <credentials>
    Then the status is 401

    Examples:
      | credentials                              |
      | no Authorization header                  |
      | a malformed Authorization header         |
      | a token signed with another secret       |
      | an expired token                         |
      | a string that is not a JWT               |

  Scenario: The 401 response names the scheme
    When a client calls GET /api/v1/annotators/me with no Authorization header
    Then the WWW-Authenticate header is "Bearer"

  Scenario: An annotator can read only their own profile
    Given a token for annotator A
    When the client asks for GET /api/v1/annotators/<B's id>
    Then the status is 403

  @nginx
  Scenario: Sign-in and sign-up share a hard rate limit in nginx
    Given the app is behind nginx
    When one address makes 5 quick attempts and then more
    Then the 6th and later get 429 as problem+json with "Retry-After: 60"
    And a sign-up attempt counts against the same limit
