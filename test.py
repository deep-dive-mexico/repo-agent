from GithubHandlers import GithubHandler
import os

ACCESS_TOKEN = "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"
api_key = "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"

GH = GithubHandler(
    repo_name="deep-dive-mexico/repo-agent",
    main_branch="main",
    auth_token=ACCESS_TOKEN,
)

print(GH.get_pr_deltas(GH.get_pr_from_id(4)))
GH.get_pr_deltas_in_list(GH.get_pr_from_id(4))

pr = GH.get_pr_from_id(4)
pr.create_review(
    body="test",
    event="REQUEST_CHANGES",
    comments=[
        {
            "path": "GithubHandler.py",
            "line": 255,
            "side": "RIGHT",
            "body": "```suggestion\n hola perro \n```",
        }
    ],
)
