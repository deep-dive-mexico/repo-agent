from CommentAgent import CommentAgent
import openai
import os

ACCESS_TOKEN = os.environ["GH_ACCESSTOKEN"]
BRANCH_OR_PR_NUMBER = os.environ[
    "PR_NUMBER"
]  # On pull request open (number), on push (branch name)
REPO_NAME = os.environ["REPO_NAME"]
openai.api_key = os.environ["OPENAI_API_KEY"]
MAIN_BRANCH = os.environ["MAIN_BRANCH"].split("/")[-1]

if __name__ == "__main__":
    agent = CommentAgent(
        repo_name=REPO_NAME,
        branch_or_prnum=BRANCH_OR_PR_NUMBER,
        branch=MAIN_BRANCH,
        github_auth_token=ACCESS_TOKEN,
    )
    agent.run()
