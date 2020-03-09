#! /usr/bin/env python
# requires python 3
#
# Do we have any open PR's that have label "Approval: done"
# that are over 24 hours without any other comments?
#
# get a token.... https://github.com/settings/tokens/new -- just repo is fine
# pop it in token.txt or you'll get a bad API limit
#
# note that we'd use pyGithub but we can't as it doesn't fully handle the timeline objects
# as of Feb 2020
#
# mark@openssl.org Feb 2020
#
import requests
import json
from datetime import datetime, timezone
from optparse import OptionParser

api_url = "https://api.github.com/repos/openssl/openssl"

def convertdate(date):
    return datetime.strptime(date.replace('Z',"+0000"), "%Y-%m-%dT%H:%M:%S%z")

# Get all the open pull requests, filtering by approval: done label

def getpullrequests():
    url = api_url + "/pulls?per_page=100&page=1"  # defaults to open
    res = requests.get(url, headers=headers)
    repos = res.json()
    prs = []
    while 'next' in res.links.keys():
        res = requests.get(res.links['next']['url'], headers=headers)
        repos.extend(res.json())

    # Let's filter by label if we're just looking to move things, we can parse
    # everything for statistics in another script

    try:
        for pr in repos:
            if 'labels' in pr:
                for label in pr['labels']:
                    if label['name'] == 'approval: done':
                        prs.append(pr['number'])
    except:
        print("failed", repos['message'])
    return prs

# Change the labels on an issue from approval: done to approval: ready to merge

def movelabeldonetoready(issue):
    url = api_url + "/issues/" + str(issue) + "/labels/approval:%20done"
    res = requests.delete(url, headers=headers)
    if (res.status_code != 200):
        print("Error removing label", res.status_code, res.content)
        return
    url = api_url + "/issues/" + str(issue) + "/labels"
    newlabel = {"labels": ["approval: ready to merge"]}
    res = requests.post(url, data=json.dumps(newlabel), headers=headers)
    if (res.status_code != 200):
        print("Error adding label", res.status_code, res.content)
        return
    newcomment = {"body":"This pull request is ready to merge"}
    url = api_url + "/issues/" + str(issue) + "/comments"
    res = requests.post(url, data=json.dumps(newcomment), headers=headers)
    if (res.status_code != 201):
        print("Error adding comment", res.status_code, res.content)
        return

# Add a comment to PR

def addcomment(issue, text):
    newcomment = {"body":text}
    url = api_url + "/issues/" + str(issue) + "/comments"
    res = requests.post(url, data=json.dumps(newcomment), headers=headers)
    if (res.status_code != 201):
        print("Error adding comment", res.status_code, res.content)

# Check through an issue and see if it's a candidate for moving

def checkpr(pr):
    url = api_url + "/issues/" + str(pr) + "/timeline?per_page=100&page=1"
    res = requests.get(url, headers=headers)
    repos = res.json()
    while 'next' in res.links.keys():
        res = requests.get(res.links['next']['url'], headers=headers)
        repos.extend(res.json())

    comments = []
    opensslmachinecomments = []
    approvallabel = {}
    readytomerge = 0
    sha = ""

    for event in repos:
        try:
            if (event['event'] == "commented"):
                if "openssl-machine" in event['actor']['login']:
                    opensslmachinecomments.append(convertdate(event["updated_at"]))
                else:
                    comments.append(convertdate(event["updated_at"]))
                if debug:
                    print("debug: commented at ",
                          convertdate(event["updated_at"]))
            if (event['event'] == "committed"):
                sha = event["sha"]
                comments.append(convertdate(event["author"]["date"]))
                if debug:
                    print("debug: created at ",
                          convertdate(event["author"]["date"]))
            elif (event['event'] == "labeled"):
                if debug:
                    print("debug: labelled with ", event['label']['name'],
                          "at", convertdate(event["created_at"]))
                approvallabel[event['label']['name']] = convertdate(
                    event["created_at"])
            elif (event['event'] == "unlabeled"):
                if (debug):
                    print("debug: unlabelled with ", event['label']['name'],
                          "at", convertdate(event["created_at"]))
                if event['label'][
                        'name'] in approvallabel:  # have to do this for if labels got renamed in the middle
                    del approvallabel[event['label']['name']]
            elif (event['event'] == "reviewed"
                  and event['state'] == "approved"):
                if debug:
                    print("debug: approved at",
                          convertdate(event['submitted_at']))
        except:
            return (repos['message'])

    if 'approval: ready to merge' in approvallabel:
        return ("issue already has label approval: ready to merge")
    if 'approval: done' not in approvallabel:
        return ("issue did not get label approval: done")
    if 'urgent' in approvallabel:
        labelurgent = approvallabel['urgent']
        if (labelurgent and max(comments) <= labelurgent):
            print("issue is urgent and has had no comments so needs a comment")
            if (options.commit):
                addcomment(pr, "@openssl/committers note this pull request has had the urgent label applied")

    approvedone = approvallabel['approval: done']

    now = datetime.now(timezone.utc)
    hourssinceapproval = (now - approvedone).total_seconds() / 3600
    if debug:
        print("Now: ", now)
        print("Last comment: ", max(comments))
        print("Approved since: ", approvedone)
        print("hours since approval", hourssinceapproval)

    if (hourssinceapproval < 24):
        return ("not yet 24 hours since labelled approval:done hours:" +
                str(int(hourssinceapproval)))

    if max(comments) > approvedone:
        if len(opensslmachinecomments) and (max(opensslmachinecomments) > approvedone):
            return ("issue had comments after approval but we have already added a comment about this")
        if (options.commit):
            addcomment(pr, "24 hours has passed since 'approval: done' was set, but as this PR has been updated in that time the label 'approval: ready to merge' is not being automatically set.  Please review the updates and set the label manually.")
        return ("issue had comments after approval: done label was given, made a comment")

    # Final check before changing the label, did CI pass?

    url = api_url + "/commits/" + sha + "/status"
    res = requests.get(url, headers=headers)
    if (res.status_code != 200):
        return("PR has unknown CI status")
    ci = res.json()
    if ci['state'] != "success":
        if len(opensslmachinecomments) and (max(opensslmachinecomments) > approvedone):
            return ("issue has CI failure but we have already added a comment about this")
        if (options.commit):
            addcomment(pr, "24 hours has passed since 'approval: done' was set, but this PR has failing CI tests.  Once the tests pass it will get moved to 'approval: ready to merge' automatically, alternatively please review and set the label manually.")
        return("PR has CI failure, made a comment")

    if (options.commit):
        print("Moving issue ", pr, " to approval: ready to merge")
        movelabeldonetoready(pr)
    else:
        print("use --commit to actually change the labels")
    return (
        "this issue was candidate to move to approval: ready to merge hours:" +
        str(int(hourssinceapproval)))

# main

parser = OptionParser()
parser.add_option("-d","--debug",action="store_true",help="be noisy",dest="debug")
parser.add_option("-t","--token",help="file containing github authentication token for example 'token 18asdjada...'",dest="token")
parser.add_option("-c","--commit",action="store_true",help="actually change the labels",dest="commit")
(options, args) = parser.parse_args()
if (options.token):
    fp = open(options.token, "r")
    git_token = fp.readline().strip('\n')
    if not " " in git_token:
       git_token = "token "+git_token
else:
    git_token = ""  # blank token is fine, but you can't change labels and you hit API rate limiting
debug = options.debug
# since timeline is a preview feature we have to enable access to it with an accept header
headers = {
    "Accept": "application/vnd.github.mockingbird-preview",
    "Authorization": git_token
}

if debug:
    print("Getting list of PRs")
prs = getpullrequests()
print("There were", len(prs), "open PRs with approval:done ")
for pr in prs:
    print(pr, checkpr(pr))

