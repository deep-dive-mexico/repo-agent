from github import Auth, Github
import os

ACCESS_TOKEN =  os.environ["GH_ACCESSTOKEN"]
# github authentication
auth = Auth.Token(ACCESS_TOKEN)
g = Github(auth=auth)


class GithubHandler:

    def __init__(self, repo_name: str = "deep-dive-mexico/aladdin-repo"):
        self.repo_name = repo_name
        # get repo
        self.repo = g.get_repo(self.repo_name)
        # get prs
        self.prs = self.repo.get_pulls(state = "open", sort = "created", base = "master")
        # get prs numbers
        self.prs_nums = [pr.number for pr in self.prs]
        
    def modify_pr(self, pr_num):
        

if __name__ == "__main__":
    print(os.environ["GH_ACCESSTOKEN"])
    print(os.environ["pull_request_number"])
    GH = GithubHandler()
    
        
