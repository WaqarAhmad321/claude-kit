# The notes flow in the order the client calls it: sign up, log in, create, read back, edit, delete.

POST {{base_url}}/api/auth/signup
[Options]
variable: email=owner-{{newUuid}}@example.test
variable: password=pw-{{newUuid}}
{
  "email": "{{email}}",
  "password": "{{password}}",
  "display_name": "Journey Owner"
}
HTTP 201
[Asserts]
header "Location" == "/api/me"
jsonpath "$.id" matches /^[0-9a-f]{24}$/
jsonpath "$.email" == "{{email}}"
jsonpath "$.password" not exists
jsonpath "$.password_hash" not exists

POST {{base_url}}/api/auth/login
{
  "email": "{{email}}",
  "password": "{{password}}"
}
HTTP 204
[Asserts]
cookie "session[HttpOnly]" exists
cookie "session[Secure]" exists
cookie "session[SameSite]" == "Lax"

GET {{base_url}}/api/me
HTTP 200
[Asserts]
jsonpath "$.email" == "{{email}}"
jsonpath "$.role" == "user"

POST {{base_url}}/api/notes
{
  "title": "First note",
  "body": "Written by the journey"
}
HTTP 201
[Captures]
note_url: header "Location"
note_id: jsonpath "$.id"
[Asserts]
header "Location" matches /^\/api\/notes\/[0-9a-f]{24}$/
jsonpath "$.title" == "First note"
jsonpath "$.created_at" isIsoDate

GET {{base_url}}{{note_url}}
HTTP 200
[Asserts]
jsonpath "$.id" == "{{note_id}}"
jsonpath "$.title" == "First note"
jsonpath "$.body" == "Written by the journey"

GET {{base_url}}/api/notes
HTTP 200
[Asserts]
jsonpath "$.results[*].id" contains "{{note_id}}"

PATCH {{base_url}}{{note_url}}
Content-Type: application/merge-patch+json
{
  "title": "Renamed note"
}
HTTP 200
[Asserts]
jsonpath "$.title" == "Renamed note"
jsonpath "$.body" == "Written by the journey"

GET {{base_url}}{{note_url}}
HTTP 200
[Asserts]
jsonpath "$.title" == "Renamed note"
jsonpath "$.body" == "Written by the journey"

DELETE {{base_url}}{{note_url}}
HTTP 204

GET {{base_url}}{{note_url}}
HTTP 404
[Asserts]
header "Content-Type" contains "application/problem+json"
