#!/usr/bin/env python3
"""
Ajoute un groupe à la allowlist CI/CD job token ("Job token permissions")
de chaque submodule d'un projet GitLab, de façon récursive.

Pour chaque submodule (et ses propres submodules, etc.), le script appelle :
    POST /projects/:id/job_token_scope/groups_allowlist  (target_group_id=<groupe>)

Ainsi, les jobs CI des projets du groupe peuvent utiliser leur CI_JOB_TOKEN
pour accéder aux submodules (ex. `git submodule update` en CI).

Usage :
    export GITLAB_URL=https://gitlab.example.com
    export GITLAB_TOKEN=glpat-xxxx   # scope "api", rôle Maintainer sur les submodules
    ./add_group_to_submodules_allowlist.py mon-groupe/mon-projet mon-groupe

Options : voir --help.
"""

import argparse
import configparser
import json
import os
import posixpath
import re
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


class GitLabError(Exception):
    def __init__(self, status, message):
        prefix = f"HTTP {status}" if isinstance(status, int) else "Erreur réseau"
        super().__init__(f"{prefix}: {message}")
        self.status = status


def force_ipv4():
    """Restreint la résolution DNS à l'IPv4 (machines où l'IPv6 est désactivé)."""
    getaddrinfo = socket.getaddrinfo

    def getaddrinfo_ipv4(host, port, family=0, *args, **kwargs):
        return getaddrinfo(host, port, socket.AF_INET, *args, **kwargs)

    socket.getaddrinfo = getaddrinfo_ipv4


def ipv6_supported():
    if not socket.has_ipv6:
        return False
    try:
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM).close()
    except OSError:
        return False
    return True


class GitLab:
    def __init__(self, url, token, ssl_context=None):
        self.url = url.rstrip("/")
        self.api = self.url + "/api/v4"
        self.token = token
        self.ssl_context = ssl_context

    def request(self, method, endpoint, params=None, data=None, raw=False):
        url = self.api + endpoint
        if params:
            url += "?" + urllib.parse.urlencode(params)
        body = None
        headers = {"PRIVATE-TOKEN": self.token}
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, context=self.ssl_context) as resp:
                content = resp.read()
                headers = resp.headers
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            raise GitLabError(e.code, msg) from None
        except urllib.error.URLError as e:
            hint = ""
            if isinstance(e.reason, ssl.SSLCertVerificationError):
                hint = " (indiquer le certificat de l'autorité avec --ca-file, ou --insecure)"
            raise GitLabError("réseau", f"{url} : {e.reason}{hint}") from None
        if raw:
            return content.decode()
        return (json.loads(content) if content else None), headers

    def get(self, endpoint, **params):
        return self.request("GET", endpoint, params=params)[0]

    def get_all(self, endpoint, **params):
        params.setdefault("per_page", 100)
        page = 1
        items = []
        while True:
            data, headers = self.request("GET", endpoint, params={**params, "page": page})
            items.extend(data)
            next_page = headers.get("X-Next-Page")
            if not next_page:
                return items
            page = int(next_page)

    def post(self, endpoint, data):
        return self.request("POST", endpoint, data=data)[0]

    @staticmethod
    def enc(value):
        return urllib.parse.quote(str(value), safe="")


def parse_gitmodules(content):
    """Retourne une liste de (path, url) depuis le contenu d'un .gitmodules."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(content)
    submodules = []
    for section in parser.sections():
        if not section.startswith("submodule"):
            continue
        path = parser.get(section, "path", fallback=None)
        url = parser.get(section, "url", fallback=None)
        if path and url:
            submodules.append((path.strip(), url.strip()))
    return submodules


def resolve_project_path(submodule_url, parent_path, gitlab_host, base_path=""):
    """
    Convertit l'URL d'un submodule en chemin de projet GitLab (namespace/projet).
    Gère les URLs relatives (../x.git), https://host/..., ssh://git@host/...
    et git@host:... Retourne None si l'URL pointe vers un autre hôte.
    base_path est le chemin de l'instance GitLab (ex. "gitlab" pour
    https://host/gitlab), retiré du début des URLs absolues.
    """
    url = submodule_url

    if url.startswith("./") or url.startswith("../"):
        # Relative au dépôt parent (comme le fait git).
        path = posixpath.normpath(posixpath.join(parent_path, url))
    else:
        scp_like = re.match(r"^(?:[^@/]+@)?([^:/]+):(?!//)(.+)$", url)
        if scp_like and "://" not in url:
            host, path = scp_like.group(1), scp_like.group(2)
        else:
            parsed = urllib.parse.urlparse(url)
            host, path = parsed.hostname, parsed.path
        if host and host.lower() != gitlab_host.lower():
            return None
        path = path.strip("/")
        if base_path and path.startswith(base_path + "/"):
            path = path[len(base_path) + 1:]

    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path


def submodule_commit(gl, project_id, ref, sub_path):
    """SHA du commit du submodule référencé par le parent (None si introuvable)."""
    parent_dir, name = posixpath.split(sub_path.strip("/"))
    params = {"ref": ref}
    if parent_dir:
        params["path"] = parent_dir
    try:
        tree = gl.get_all(f"/projects/{project_id}/repository/tree", **params)
    except GitLabError:
        return None
    for entry in tree:
        if entry["name"] == name and entry["type"] == "commit":
            return entry["id"]
    return None


def get_gitmodules(gl, project_id, ref):
    try:
        return gl.request(
            "GET",
            f"/projects/{project_id}/repository/files/.gitmodules/raw",
            params={"ref": ref},
            raw=True,
        )
    except GitLabError as e:
        if e.status == 404:
            return None
        raise


def add_group_to_allowlist(gl, project, group, dry_run):
    pid = project["id"]
    name = project["path_with_namespace"]
    try:
        allowlist = gl.get_all(f"/projects/{pid}/job_token_scope/groups_allowlist")
    except GitLabError as e:
        print(f"  [ERREUR] {name}: lecture de la allowlist impossible ({e})")
        return "error"
    if any(g["id"] == group["id"] for g in allowlist):
        print(f"  [OK]     {name}: groupe déjà présent")
        return "present"
    if dry_run:
        print(f"  [DRY]    {name}: le groupe serait ajouté")
        return "added"
    try:
        gl.post(
            f"/projects/{pid}/job_token_scope/groups_allowlist",
            {"target_group_id": group["id"]},
        )
    except GitLabError as e:
        print(f"  [ERREUR] {name}: ajout impossible ({e})")
        return "error"
    print(f"  [AJOUT]  {name}: groupe ajouté")
    return "added"


def walk(gl, project, ref, group, args, visited, stats, depth=0):
    indent = "  " * depth
    content = get_gitmodules(gl, project["id"], ref)
    if not content:
        return
    for sub_path, sub_url in parse_gitmodules(content):
        target = resolve_project_path(
            sub_url, project["path_with_namespace"], args.gitlab_host, args.gitlab_base_path
        )
        print(f"{indent}- {sub_path} -> {sub_url}")
        if target is None:
            print(f"{indent}  [SKIP]   hôte différent de {args.gitlab_host}")
            stats["skipped"] += 1
            continue
        try:
            sub_project = gl.get(f"/projects/{gl.enc(target)}")
        except GitLabError as e:
            print(f"{indent}  [ERREUR] projet '{target}' introuvable ({e})")
            stats["error"] += 1
            continue

        if sub_project["id"] in visited:
            print(f"{indent}  [OK]     {target}: déjà traité")
            continue
        visited.add(sub_project["id"])

        result = add_group_to_allowlist(gl, sub_project, group, args.dry_run)
        stats[result] += 1

        if args.no_recursive:
            continue
        sub_ref = None
        if not args.use_default_branch:
            sub_ref = submodule_commit(gl, project["id"], ref, sub_path)
        sub_ref = sub_ref or sub_project.get("default_branch")
        if sub_ref:
            walk(gl, sub_project, sub_ref, group, args, visited, stats, depth + 1)


def main():
    parser = argparse.ArgumentParser(
        description="Ajoute un groupe à la allowlist CI/CD job token de chaque "
        "submodule (récursivement) d'un projet GitLab."
    )
    parser.add_argument("project", help="Projet racine (ID ou chemin, ex. groupe/projet)")
    parser.add_argument("group", help="Groupe à ajouter (ID ou chemin, ex. groupe/sous-groupe)")
    parser.add_argument("--ref", help="Branche/tag/commit du projet racine (défaut : branche par défaut)")
    parser.add_argument("--include-root", action="store_true",
                        help="Ajoute aussi le groupe à la allowlist du projet racine")
    parser.add_argument("--no-recursive", action="store_true",
                        help="Ne traite que les submodules directs")
    parser.add_argument("--use-default-branch", action="store_true",
                        help="Lit les .gitmodules des submodules sur leur branche par défaut "
                        "au lieu du commit référencé par le parent")
    parser.add_argument("--dry-run", action="store_true",
                        help="Affiche ce qui serait fait sans rien modifier")
    parser.add_argument("--gitlab-url", default=os.environ.get("GITLAB_URL", "https://gitlab.com"),
                        help="URL de l'instance GitLab (défaut : $GITLAB_URL ou https://gitlab.com)")
    parser.add_argument("--gitlab-host",
                        help="Hôte utilisé dans les URLs des submodules, si différent de celui "
                        "de --gitlab-url (ex. hôte SSH)")
    parser.add_argument("--ca-file",
                        default=os.environ.get("GITLAB_CA_FILE") or os.environ.get("REQUESTS_CA_BUNDLE"),
                        help="Certificat (PEM) de l'autorité qui signe le certificat HTTPS de GitLab "
                        "(défaut : $GITLAB_CA_FILE ou $REQUESTS_CA_BUNDLE)")
    parser.add_argument("--insecure", action="store_true",
                        help="Désactive la vérification du certificat HTTPS (déconseillé)")
    parser.add_argument("--ipv4", action="store_true",
                        help="Force les connexions en IPv4 (automatique si l'IPv6 est indisponible)")
    args = parser.parse_args()

    if args.ipv4 or not ipv6_supported():
        force_ipv4()

    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        sys.exit("Variable d'environnement GITLAB_TOKEN manquante.")
    args.gitlab_host = args.gitlab_host or urllib.parse.urlparse(args.gitlab_url).hostname
    args.gitlab_base_path = urllib.parse.urlparse(args.gitlab_url).path.strip("/")

    if args.insecure:
        print("ATTENTION : vérification du certificat HTTPS désactivée.", file=sys.stderr)
        ssl_context = ssl._create_unverified_context()
    else:
        try:
            ssl_context = ssl.create_default_context(cafile=args.ca_file)
        except (OSError, ssl.SSLError) as e:
            sys.exit(f"Impossible de charger le certificat '{args.ca_file}' : {e}")

    gl = GitLab(args.gitlab_url, token, ssl_context)
    try:
        project = gl.get(f"/projects/{gl.enc(args.project)}")
        group = gl.get(f"/groups/{gl.enc(args.group)}", with_projects="false")
    except GitLabError as e:
        sys.exit(f"Erreur : {e}")

    ref = args.ref or project.get("default_branch")
    print(f"Projet racine : {project['path_with_namespace']} (ref {ref})")
    print(f"Groupe        : {group['full_path']} (id {group['id']})")
    if args.dry_run:
        print("Mode dry-run : aucune modification ne sera effectuée.")
    print()

    stats = {"added": 0, "present": 0, "skipped": 0, "error": 0}
    visited = {project["id"]}
    if args.include_root:
        stats[add_group_to_allowlist(gl, project, group, args.dry_run)] += 1
    walk(gl, project, ref, group, args, visited, stats)

    print()
    print(f"Ajoutés : {stats['added']}, déjà présents : {stats['present']}, "
          f"ignorés : {stats['skipped']}, erreurs : {stats['error']}")
    sys.exit(1 if stats["error"] else 0)


if __name__ == "__main__":
    main()
