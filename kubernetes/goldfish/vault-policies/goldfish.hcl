path "goldfish-secret/runtime-config" {
  capabilities = ["read", "update"]
}

path "goldfish-transit/encrypt/server" {
  capabilities = ["read", "update"]
}
path "goldfish-transit/decrypt/server" {
  capabilities = ["read", "update"]
}
