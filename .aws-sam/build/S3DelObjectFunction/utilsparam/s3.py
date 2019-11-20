import os
import datetime
import json

import boto3
from botocore.errorfactory import ClientError

#####from .config import REGION, LOGDIR, LOGFILE

# Boto3 Resources & Clients
s3_resource = boto3.resource('s3')
s3_client = boto3.client('s3', region_name=os.environ['REGION'])


def mkdir(bucket, path, dirname):
    """ Makes new directory path in bucket
    :param bucket: s3 bucket object within which directory is being created
    :param path: string local path where directory is to be created
    :param dirname: string name of directory to be created
    :return: path to new directory
    """
    new_path = os.path.join(path, dirname, '')
    try:
        s3_client.head_object(Bucket=bucket, Key=new_path)
    except ClientError:
        s3_client.put_object(Bucket=bucket, Key=new_path)
    return new_path


def ls(bucket, path):
    """ Get all objects with bucket as strings"""
    return [
        objname.key for objname in bucket.objects.filter(Prefix=path)
    ]

def exists(bucket_name, path):
    """ checks if there is any data under the given (Prefix) path for the given bucket. """
    bucket = s3_resource.Bucket(bucket_name)
    objlist = [objname.key for objname in bucket.objects.filter(Prefix=path)]
    return len(objlist) >0

def load_json(bucket_name, key):
    """ """
    file_object = s3_resource.Object(bucket_name, key)
    raw_content = file_object.get()['Body'].read().decode('utf-8')
    json_content = json.loads(raw_content)
    ## Transfer type 
    return json_content 

def extract_files(bucket_name,prefix,ext = None):
    """
    Filters out the actual filenames being used to process data from the prefix that is given. 
    Inputs:
    bucket_name (str): the name of the s3 bucket. 
    prefix (str): the "folder name" that we care about 
    ext (optional): if provided, will filter filenames to be of the given extension. 
    """
    bucket = s3_resource.Bucket(bucket_name)
    objgen = bucket.objects.filter(Prefix = prefix)
    if ext is None:
        file_list = [obj.key for obj in objgen if obj.key[-1] != "/"]
    else:
        file_list = [obj.key for obj in objgen if obj.key[-1] != "/" and obj.key.split(".")[-1] == ext]

    return file_list 

class WriteMetric():
    """ Utility Class For Benchmarking performance """

    def __init__(self, bucket_name, path,instance,time):
        """ """
        self.bucket = s3_resource.Bucket(bucket_name) 
        self.instance = instance
        self.path = os.path.join(path, instance, '')#mkdir(bucket_name, path,instance)
        self.time = time
        self._logs = []

    def append(self, string):
        """ """
        self._logs.append(
            self.time + ": " + string + "\n"
        )

    def write(self):
        """ """
        encoded_text = "\n".join(self._logs).encode("utf-8")
        self.bucket.put_object(
            Key=os.path.join(self.path,self.time+'.txt'),
            Body=encoded_text
        )

class Logger():
    """ Utility Class For Collection Logs & Writing To S3 """

    def __init__(self, bucket_name, path):
        """ """
        self.bucket = s3_resource.Bucket(bucket_name) 
        self.path = os.path.join(path, os.environ['LOGDIR'],'')#mkdir(bucket_name, path, LOGDIR)
        self._logs = []

    def append(self, string):
        """ """
        self._logs.append(
            str(datetime.datetime.now()) + ": " + string + "\n"
        )

    def write(self):
        """ """
        encoded_text = "\n".join(self._logs).encode("utf-8")
        self.bucket.put_object(
            Key=os.path.join(self.path, os.environ['LOGFILE']),
            Body=encoded_text
        )

class JobLogger(Logger):
    """
    Updated utility class to write logs in format amenable to figure updating. 
    """
    def __init__(self,bucket_name,path):
        self.bucket = s3_resource.Bucket(bucket_name) 
        self.bucket_name = bucket_name
        self.path = os.path.join(path, os.environ['LOGDIR'],"certificate.txt")#mkdir(bucket_name, path, LOGDIR)
        ## Stupid, fix this TODO
        self.basepath = path
        ## Declare the object you will write to: 
        self.writeobj = s3_resource.Object(bucket_name,self.path)
        self._logs = []
        self._datasets = {}
        self._config = {}
        self._struct = {"logs":"no logs","datasets":"data not loaded","config":"config not loaded"}

    def append_lambdalog(self,string):
        """
        Unambiguously named wrapper for append. 
        Inputs: 
        string: the string to append to the lambda log. 
        """
        self.append(string)

    def initialize_datasets_dev(self,dataset,instanceid,commandid):
        """
        Initialize datasets by assigning to each a dictionary specifying instance data will be run on, command id, status, job description, reason, most recent output. 
        Inputs:
        dataset: the path to the data *file* analyzed by the instance. 
        instanceid (str): the string specifying what instance we will run analysis on. 
        commandid (str): the string specifying what the command id corresonding to this instance is. 
        """
        template_dict = {"status":"INITIALIZING","reason":"NONE","stdout":"not given yet","stderr":"not given yet","input":dataset,"instance":instanceid,"command":commandid}
        ##TODO: check that these instances and commands exist. 
        self._datasets[dataset] = template_dict
        dataset_basename = os.path.basename(dataset)
        datapath = os.path.join(self.basepath, os.environ['LOGDIR'],"DATASET_NAME:"+dataset_basename+"_STATUS.txt")#mkdir(bucket_name, path, LOGDIR)
        dataobj = s3_resource.Object(self.bucket_name,datapath)
        dataobj.put(Body = (bytes(json.dumps(template_dict).encode("UTF-8"))))

    def initialize_datasets(self,dataset,instanceid,commandid):
        """
        Initialize datasets by assigning to each a dictionary specifying instance data will be run on, command id, status, job description, reason, most recent output. 
        Inputs:
        dataset: the path to the data *file* analyzed by the instance. 
        instanceid (str): the string specifying what instance we will run analysis on. 
        commandid (str): the string specifying what the command id corresonding to this instance is. 
        """
        template_dict = {"status":"INITIALIZING","reason":"NONE","stdout":"not given yet","stderr":"not given yet","instance":instanceid,"command":commandid}
        ##TODO: check that these instances and commands exist. 
        self._datasets[dataset] = template_dict
        ## Additionally (later substitute), write these datasets to their own objects.`

    def assign_config(self,configpath):
        """
        Configuration assignment. Includes version of config file .
        Inputs: 
        configpath (str): path to config file
        """
        self._config['name'] = configpath # TODO Turn on versioning for user buckets so we can trace configs. 

    def update(self):
        """
        Updates the struct object. 
        """
        self._struct['logs'] = self._logs
        self._struct['datasets'] = self._datasets
        self._struct['config'] = self._config

    def write(self):
        """ 
        Updates the struct object, and writes the resulting dictionary.  
        """
        self.update()
        self.writeobj.put(Body = (bytes(json.dumps(self._struct).encode("UTF-8"))))
        


#def check_for_config(upload, config):
#    """ """
#    contents = ls(bucket=bucket, path=local_path)
#    assert (os.path.joing(local_path, CONFIG) in contents), MISSING_CONFIG_ERROR
