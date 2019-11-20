"""
Script to export credentials generated by creation of a user stack (as compiled by user_maker.py) 

Inputs: 
    path (str): the path to user stack directory you would like to generate credentials for.

"""
import sys
import os
import boto3
import json
import csv
import re

if __name__ == "__main__":
    ## Get the stackname
    path = sys.argv[1]
    if path[-1] == "/":
        path = path[:-1]
    stackname = os.path.basename(path)
    ## Get information from the config file:
    config = json.load(open(os.path.join(path,"user_config_template.json")))
    region = config["Lambda"]["LambdaConfig"]["REGION"]

    ## Format the structure you want the user dictionary to have: 
    all_users = []
    for affiliate in config["UXData"]["Affiliates"]:
        all_users = all_users+affiliate['UserNames']

    user_dict = {user:{"Username":user} for user in all_users}
    ## Now get the stack info:  
    cfnclient = boto3.client("cloudformation",region_name = region)
    ## Get the outputs of the stack:
    outputs = cfnclient.describe_stacks(StackName = stackname)["Stacks"][0]["Outputs"]
    ## Now write the outputs to  
    for out in outputs:
        key = out["OutputKey"]
        ## If this is a Key or Secret Key, we want to keep it. 
        Access = re.findall(r"^AccessKey(.+)",key)
        SecretAccess = re.findall(r"^SecretAccessKey(.+)",key)
        if Access:
            assert len(Access) == 1,"key improperly filtered"
            user_dict[Access[0]]["Access Key"] = out["OutputValue"]
        elif SecretAccess:
            assert len(SecretAccess) == 1, "key improperly filtered"
            user_dict[SecretAccess[0]]["Secret Access Key"] = out["OutputValue"]

    ## Now let's write out to a csv: 
    out_dest = "/Users/taigaabe/ncap_user_creds/"
    path_prefix = "NCAP_KEY_AUTO_{}.csv"
    save_path = os.path.join(out_dest,stackname)
    if not os.path.exists(save_path):
        os.mkdir(save_path)
    for user in all_users:
        csv_fullpath = os.path.join(out_dest,stackname,path_prefix.format(user))
        fieldnames = ["Username","Access Key", "Secret Access Key"]
        with open(csv_fullpath,mode = "w") as file:
            writer = csv.DictWriter(file,fieldnames=fieldnames,delimiter = ",")
            writer.writeheader()
            writer.writerow(user_dict[user])

    


    
    
    