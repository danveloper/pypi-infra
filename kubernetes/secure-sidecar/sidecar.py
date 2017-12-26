import os
import time

import click
import requests


def wrapping_token_lookup(vault_ca_file, vault_addr, token):
    response = requests.post(f'{vault_addr}/v1/sys/wrapping/lookup',
                             json={"token": token},
                             verify=vault_ca_file)
    response.raise_for_status()
    return response


def token_lookup_self(vault_ca_file, vault_addr, token):
    response = requests.get(f'{vault_addr}/v1/auth/token/lookup-self',
                            headers={'X-Vault-Token': token},
                            verify=vault_ca_file)
    response.raise_for_status()
    return response


def token_renew_self(vault_ca_file, vault_addr, token):
    response = requests.post(f'{vault_addr}/v1/auth/token/renew-self',
                             headers={'X-Vault-Token': token},
                             json={},
                             verify=vault_ca_file)
    response.raise_for_status()
    return response


def unwrap_vault_response(vault_ca_file, vault_addr, wrapping_token):
    response = requests.post(f'{vault_addr}/v1/sys/wrapping/unwrap',
                             headers={'X-Vault-Token': wrapping_token},
                             verify=vault_ca_file)
    response.raise_for_status()
    return response


def vault_kubernetes_auth_login(vault_ca_file, vault_addr, vault_backend, jwt, vault_role, wrap, unwrap):
    headers = {}
    if wrap:
        headers['X-Vault-Wrap-TTL'] = '60s'
    token = requests.post(f'{vault_addr}/v1/{vault_backend}',
                          headers=headers,
                          json={'jwt': jwt, 'role': vault_role},
                          verify=vault_ca_file)
    token.raise_for_status()
    if wrap:
        click.echo(f'fetched wrapped token with accessor {token.json()["wrap_info"]["accessor"]}')
        if unwrap:
            click.echo(f'unwrapping accessor {token.json()["wrap_info"]["accessor"]}')
            token = unwrap_vault_response(vault_ca_file, vault_addr, token.json()['wrap_info']['token'])
            click.echo(f'fetched unwrapped token with accessor {token.json()["auth"]["accessor"]}')
    else:
        click.echo(f'fetched token with accessor {token.json()["auth"]["accessor"]}')
    return token.json()


def service_dns(service_name, namespace, domain):
    return [
        f'{service_name}.{namespace}.svc.{domain}',
        f'{service_name}.{namespace}.svc',
        f'{service_name}.{namespace}',
        f'{service_name}',
    ]


def pod_dns(pod_ip, namespace, domain):
    return [
        f'{pod_ip.replace(".", "-")}.{namespace}.pod.{domain}',
        f'{pod_ip.replace(".", "-")}.{namespace}.pod',
    ]


def headless_dns(hostname, subdomain, namespace, domain):
    return [
        f'{hostname}.{subdomain}.{namespace}.svc.{domain}',
        f'{hostname}.{subdomain}.{namespace}.svc',
        f'{hostname}.{subdomain}.{namespace}',
        f'{hostname}.{subdomain}',
        f'{hostname}',
    ]


def request_vault_certificate(vault_addr, vault_token, vault_ca_file, vault_pki_backend, vault_pki_role, common_name, alt_names, ip_sans):
    response = requests.post(f'{vault_addr}/v1/{vault_pki_backend}/issue/{vault_pki_role}',
                             json={"common_name": common_name, "alt_names": ','.join(alt_names), "ip_sans": ','.join(ip_sans)},
                             headers={'X-Vault-Token': vault_token},
                             verify=vault_ca_file)
    print(response.content)
    response.raise_for_status()
    return response


@click.group()
def cli():
    pass


@cli.command()
@click.option('--namespace', default="default", help="namespace as defined by pod.metadata.namespace")
@click.option('--vault-addr', default="https://vault-server.vault.svc.cluster.local", help="Vault Address to request for Kubernetes Auth.")
@click.option('--vault-ca-file', default="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt", help="Certificate Authority to verify Vault TLS.")
@click.option('--vault-kubernetes-auth-role', default=None, help="Vault Role to request for Kubernetes Auth.")
@click.option('--vault-kubernetes-auth-backend', default="auth/kubernetes/login", help="Path to attempt Vault Kubernetes Auth against")
@click.option('--vault-kubernetes-auth-token-path', default="/var/run/secrets/vault/", help="Directory to store vault-token file in", type=click.Path(exists=True))
@click.option('--wrap/--no-wrap', default=False, help="Use Vault Response Wrapping when requesting tokens, etc")
@click.option('--unwrap/--no-unwrap', default=False, help="Unwrap Vault Token response, may not be desirable for some apps")
def kube_login(namespace, vault_addr, vault_ca_file, vault_kubernetes_auth_role, vault_kubernetes_auth_backend, vault_kubernetes_auth_token_path, wrap, unwrap):
    if vault_kubernetes_auth_role:
        click.echo(f'Attempting Vault Auth Login with Kubernetes for {namespace}-{vault_kubernetes_auth_role}')
        click.echo('reading jwt for vault kubernetes auth')
        with open('/var/run/secrets/kubernetes.io/serviceaccount/token', 'rU') as f:
            jwt = f.read()
        click.echo('fetching vault token')
        token = vault_kubernetes_auth_login(
            vault_ca_file,
            vault_addr,
            vault_kubernetes_auth_backend,
            jwt,
            f'{namespace}-{vault_kubernetes_auth_role}',
            wrap,
            unwrap,
        )
        if (wrap and unwrap) or not wrap:
            token_type = 'vault-token'
            token_path = os.path.join(vault_kubernetes_auth_token_path, 'vault-token')
            token_contents = token["auth"]["client_token"]
        else:
            token_type = 'wrapped-vault-token'
            token_path = os.path.join(vault_kubernetes_auth_token_path, 'wrapped-vault-token')
            token_contents = token["wrap_info"]["token"]
        click.echo(f'writing {token_type} to {token_path}')
        with open(token_path, 'w') as f:
            f.write(token_contents)


@cli.command()
@click.option('--vault-addr', default="https://vault-server.vault.svc.cluster.local", help="Vault address to communicate with.")
@click.option('--vault-ca-file', default="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt", help="Certificate Authority to verify Vault TLS.")
@click.option('--vault-pki-backend', default="cabotage-ca", help="Vault PKI backend to request certificate from.")
@click.option('--vault-pki-role', required=True, help="Vault PKI role to request certificate from.")
@click.option('--token-file', default="/var/run/secrets/vault/vault-token", help="Path Vault Token is stored at", type=click.File(mode='rU'))
@click.option('--cert-dir', default="/etc/tls", help="directory to store tls key and cert", type=click.Path(exists=True))
@click.option('--hostname', default="", help="hostname as defined by pod.spec.hostname")
@click.option('--subdomain', default="", help="subdomain as defined by pod.spec.subdomain")
@click.option('--namespace', default="default", help="namespace as defined by pod.metadata.namespace")
@click.option('--cluster-domain', default="cluster.local", help="kubernetes cluster domain")
@click.option('--pod-name', required=True, help="name as defined by pod.metadata.name")
@click.option('--pod-ip', required=True, help="pod IP address as defined by pod.status.podIP")
@click.option('--additional-dnsnames', default="", help="additional dns names; comma separated")
@click.option('--service-names', default="", help="service names that resolve to this Pod; comma separated")
@click.option('--service-ips', default="", help="service IP addresses that resolve to this Pod; comma separated")
def fetch_vault_cert(vault_addr, vault_ca_file, vault_pki_backend, vault_pki_role, token_file, cert_dir,
                     hostname, subdomain, namespace, cluster_domain,
                     pod_name, pod_ip, additional_dnsnames, service_names, service_ips):
    dnsnames = pod_dns(pod_ip, namespace, cluster_domain)
    dnsnames += [x for x in additional_dnsnames.split(',') if x]
    for service_name in service_names.split(','):
        if service_name:
            dnsnames += service_dns(service_name, namespace, cluster_domain)

    if hostname and subdomain:
        dnsnames += headless_dns(hostname, subdomain, namespace, cluster_domain)

    ips = [pod_ip]
    ips += [x for x in service_ips.split(',') if x]

    common_name = dnsnames[0]

    certificate_response = request_vault_certificate(vault_addr, token_file.read(), vault_ca_file, vault_pki_backend, vault_pki_role, common_name, set(dnsnames), set(ips))
    certificate_data = certificate_response.json()['data']
    from pprint import pprint as pp
    pp(certificate_data)


@cli.command()
@click.option('--vault-addr', default="https://vault-server.vault.svc.cluster.local", help="Vault address to communicate with.")
@click.option('--vault-ca-file', default="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt", help="Certificate Authority to verify Vault TLS.")
@click.option('--token-file', default="/var/run/secrets/vault/vault-token", help="Path Vault Token is stored at", type=click.File(mode='rU'))
@click.option('--write-token-file', default="/var/run/secrets/vault/vault-token", help="Path Vault Token is stored at", type=click.File(mode='w'))
@click.option('--unwrap/--no-unwrap', default=False, help="Unwrap stored vault token, may not be desirable for some apps")
def fetch_and_renew(vault_addr, vault_ca_file, token_file, write_token_file, unwrap):
    token = token_file.read()
    if unwrap:
        click.echo("Unwrapping from stored wrapped token")
        try:
            response = wrapping_token_lookup(vault_ca_file, vault_addr, token)
            response.raise_for_status()
        except Exception as e:
            click.echo("Issue looking up wrapping token ID!: %s" % (e,))
            click.echo("Something may be amiss!")
            click.Abort()
        token = unwrap_vault_response(vault_ca_file, vault_addr, token).json()["auth"]["client_token"]
        write_token_file.write(token)
        write_token_file.close()
    token_info = token_lookup_self(vault_ca_file, vault_addr, token).json()
    click.echo(f'Using token with accessor {token_info["data"]["accessor"]} and policies {", ".join(token_info["data"]["policies"])}')

    while True:
        min_sleep = 60
        click.echo(f'checking vault token with accessor {token_info["data"]["accessor"]}')
        token_info = token_lookup_self(vault_ca_file, vault_addr, token).json()
        if token_info['data']['renewable']:
            if token_info['data']['ttl'] < int(token_info['data']['creation_ttl']/2):
                click.echo(f'renewing vault token with accessor {token_info["data"]["accessor"]}')
                token_renew_self(vault_ca_file, vault_addr, token)
                sleep = min_sleep
            else:
                sleep = max(min_sleep, int(token_info['data']['ttl']/4))
        click.echo(f'sleeping {sleep} seconds...')
        time.sleep(sleep)


if __name__ == '__main__':
    cli()
