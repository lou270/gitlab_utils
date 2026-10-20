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
| `--gitlab-url URL` | URL de l'instance (défaut : `$GITLAB_URL` ou `https://gitlab.com`) |
| `--gitlab-host HOST` | Hôte présent dans les URLs des submodules s'il diffère de celui de `--gitlab-url` |
| `--ipv4` | Force les connexions en IPv4 (activé automatiquement si l'IPv6 est indisponible sur la machine) |

Les URLs de submodules relatives (`../x.git`), HTTPS et SSH sont supportées ; celles pointant
vers un autre hôte sont ignorées. Le script est idempotent (les groupes déjà présents sont sautés)
et retourne un code de sortie 1 en cas d'erreur.
