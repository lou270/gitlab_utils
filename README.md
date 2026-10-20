# gitlab_utils

## add_group_to_submodules_allowlist.py

Ajoute un groupe à la allowlist CI/CD job token (*Settings > CI/CD > Job token permissions*)
de chaque submodule d'un projet GitLab, **récursivement** (submodules de submodules, etc.).
Utile pour que les jobs CI des projets du groupe puissent cloner les submodules avec `CI_JOB_TOKEN`.

Python 3 uniquement, aucune dépendance.

```bash
export GITLAB_URL=https://gitlab.example.com
export GITLAB_TOKEN=glpat-xxxx   # scope "api", Maintainer sur les submodules

./add_group_to_submodules_allowlist.py mon-groupe/mon-projet mon-groupe --dry-run
./add_group_to_submodules_allowlist.py mon-groupe/mon-projet mon-groupe
```

Options :

| Option | Description |
|---|---|
| `--ref REF` | Branche/tag/commit du projet racine (défaut : branche par défaut) |
| `--include-root` | Ajoute aussi le groupe à la allowlist du projet racine |
| `--no-recursive` | Ne traite que les submodules directs |
| `--use-default-branch` | Lit les `.gitmodules` des submodules sur leur branche par défaut plutôt que sur le commit référencé par le parent |
| `--dry-run` | N'effectue aucune modification |
| `--gitlab-url URL` | URL de l'instance, éventuellement avec un chemin, ex. `https://host/gitlab` (défaut : `$GITLAB_URL` ou `https://gitlab.com`) |
| `--gitlab-host HOST` | Hôte présent dans les URLs des submodules s'il diffère de celui de `--gitlab-url` |
| `--ca-file FICHIER` | Certificat (PEM) de l'autorité qui signe le certificat HTTPS de GitLab (défaut : `$GITLAB_CA_FILE` ou `$REQUESTS_CA_BUNDLE`) |
| `--insecure` | Désactive la vérification du certificat HTTPS (déconseillé, pour dépanner) |
| `--ipv4` | Force les connexions en IPv4 (activé automatiquement si l'IPv6 est indisponible sur la machine) |

Les URLs de submodules relatives (`../x.git`), HTTPS et SSH sont supportées ; celles pointant
vers un autre hôte sont ignorées. Le script est idempotent (les groupes déjà présents sont sautés)
et retourne un code de sortie 1 en cas d'erreur.

### Erreur `CERTIFICATE_VERIFY_FAILED`

Le GitLab utilise un certificat signé par une autorité interne (ou auto-signé). Récupérer le
certificat de l'autorité (format PEM) et le passer au script :

```bash
./add_group_to_submodules_allowlist.py groupe/projet groupe --ca-file /chemin/ca.pem
```

Pour récupérer la chaîne de certificats présentée par le serveur :

```bash
openssl s_client -showcerts -connect gitlab.example.com:443 </dev/null 2>/dev/null \
  | sed -n '/BEGIN CERT/,/END CERT/p' > ca.pem
```
