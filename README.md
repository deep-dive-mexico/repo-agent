# repo-agent
GPT agent integration with repositories,  it makes comments on pull requests or even suggest changes.

## How to add to your project
First add it as a github submodule:
```
git submodule add https://github.com/deep-dive-mexico/repo-agent.git
```

Second setup the Githuh Actions workflows:

1. Create a YAML file in the path .github/workflows

There you should add the following:

```yaml
name: Comment on Pull Request

on:
  pull_request:
    types: [create, review_requested, ready_for_review]

jobs:
  comment:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository and submodules
        uses: actions/checkout@v2
        with:
          submodules: recursive
      - name: Comment on PR
        env:
          GH_ACCESSTOKEN: ${{ secrets.GH_ACCESSTOKEN }}
          REPO_NAME: ${{ github.repository }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY}}
          MAIN_BRANCH: ${{github.base_ref}}

        run: |
          export PR_NUMBER=$(echo $GITHUB_REF | awk 'BEGIN { FS = "/" } ; { print $3 }')
          cd repo-agent 
          make install-commenter
          make comment-pr
          echo "Successfully commented on PR: $PR_NUMBER"
```

Finally in the repository settings you should add the secrets:

- GH_ACCESSTOKEN: being an access token with read access to the repository and write access to pull requests
- OPENAI_API_KEY: An openai api key with access to the latest models


## Optional Tuning
The agent should work outside the box as it is, but you can also add custom instructions like examples of good practices or tone, there is a sample in this repository.
The way you add this is adding a file named README.md (case sensitive) in the path: agent-settings/README.md, dont be shy in your instructions but beware of context window, so dont abuse it.



