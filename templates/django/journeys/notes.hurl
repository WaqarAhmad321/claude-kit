# The notes flow in the order the client calls it: sign up, log in, create, read back, edit, delete.

POST {{base_url}}/api/v1/auth/signup/
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
jsonpath "$.email" == "{{email}}"
jsonpath "$.password" not exists

POST {{base_url}}/api/v1/auth/token/
{
  "email": "{{email}}",
  "password": "{{password}}"
}
HTTP 200
[Captures]
access: jsonpath "$.access"

GET {{base_url}}/api/v1/users/me/
Authorization: Bearer {{access}}
HTTP 200
[Asserts]
jsonpath "$.email" == "{{email}}"
jsonpath "$.role" == "user"

POST {{base_url}}/api/v1/notes/
Authorization: Bearer {{access}}
{
  "title": "First note",
  "body": "Written by the journey"
}
HTTP 201
[Captures]
note_url: header "Location"
note_id: jsonpath "$.id"
[Asserts]
header "Location" matches /^\/api\/v1\/notes\/\d+\/$/
jsonpath "$.title" == "First note"
jsonpath "$.created_at" isIsoDate

GET {{base_url}}{{note_url}}
Authorization: Bearer {{access}}
HTTP 200
[Asserts]
jsonpath "$.id" == {{note_id}}
jsonpath "$.body" == "Written by the journey"

GET {{base_url}}/api/v1/notes/
Authorization: Bearer {{access}}
HTTP 200
[Asserts]
jsonpath "$.count" >= 1
jsonpath "$.results[*].id" contains {{note_id}}

PATCH {{base_url}}{{note_url}}
Authorization: Bearer {{access}}
Content-Type: application/merge-patch+json
{
  "title": "Renamed note"
}
HTTP 200
[Asserts]
jsonpath "$.title" == "Renamed note"
jsonpath "$.body" == "Written by the journey"

DELETE {{base_url}}{{note_url}}
Authorization: Bearer {{access}}
HTTP 204

GET {{base_url}}{{note_url}}
Authorization: Bearer {{access}}
HTTP 404
[Asserts]
header "Content-Type" contains "application/problem+json"
