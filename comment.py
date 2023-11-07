from github import Auth, Github
import os

ACCESS_TOKEN =  os.environ["GH_ACCESSTOKEN"]

class GithubHandler:

    def __init__(self, repo_name: str = "deep-dive-mexico/aladdin-repo"):
        # github authentication
        self.auth = Auth.Token(ACCESS_TOKEN)
        self.g = Github(auth=self.auth)

        self.repo_name = repo_name
        # get repo
        self.repo = g.get_repo(self.repo_name)
        # get prs
        self.prs = self.repo.get_pulls(state = "open", sort = "created", base = "master")
        # get prs numbers
        self.prs_nums = [pr.number for pr in self.prs]
        # create dict of prs
        self.prs_dict = {k: v for k, v in zip(self.prs_nums, self.prs)}

    def modify_pr(self, pr_num: int):
        pass

    def get_file(self, filename: str):
        pass


if __name__ == "__main__":
    print(os.environ["GH_ACCESSTOKEN"])
    print(os.environ["pull_request_number"])
    GH = GithubHandler()
    
        
