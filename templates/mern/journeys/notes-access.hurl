# Object-level access: another user can't read, change or delete someone else's note,
# and gets 404 rather than 403 so the note's existence stays hidden.

POST {{base_url}}/api/auth/signup
[Options]
variable: owner_email=owner-{{newUuid}}@example.test
variable: other_email=other-{{newUuid}}@example.test
variable: password=pw-{{newUuid}}
{
  "email": "{{owner_email}}",
  "password": "{{password}}",
  "display_name": "Journey Owner"
}
HTTP 201

POST {{base_url}}/api/auth/signup
{
  "email": "{{other_email}}",
  "password": "{{password}}",
  "display_name": "Journey Other"
}
HTTP 201

POST {{base_url}}/api/auth/login
{
  "email": "{{owner_email}}",
  "password": "{{password}}"
}
HTTP 204

POST {{base_url}}/api/notes
{
  "title": "Private note",
  "body": "Only the owner sees this"
}
HTTP 201
[Captures]
note_url: header "Location"
note_id: jsonpath "$.id"

POST {{base_url}}/api/auth/login
{
  "email": "{{other_email}}",
  "password": "{{password}}"
}
HTTP 204

GET {{base_url}}{{note_url}}
HTTP 404

PATCH {{base_url}}{{note_url}}
Content-Type: application/merge-patch+json
{
  "title": "Taken over"
}
HTTP 404

DELETE {{base_url}}{{note_url}}
HTTP 404

GET {{base_url}}/api/notes
HTTP 200
[Asserts]
jsonpath "$.results[*].id" not contains "{{note_id}}"

POST {{base_url}}/api/auth/login
{
  "email": "{{owner_email}}",
  "password": "{{password}}"
}
HTTP 204

GET {{base_url}}{{note_url}}
HTTP 200
[Asserts]
jsonpath "$.title" == "Private note"
