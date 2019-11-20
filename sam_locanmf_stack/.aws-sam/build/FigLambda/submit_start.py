import os
import json
import traceback
import re


try:
    #import utils_param.config as config
    import utilsparam.s3
    import utilsparam.ssm
    import utilsparam.ec2
    import utilsparam.events
except Exception as e:
    error = str(e)
    stacktrace = json.dumps(traceback.format_exc())
    message = "Exception: " + error + "  Stacktrace: " + stacktrace
    err = {"message": message}
    print(err)


def respond(err, res=None):
    return {
        "statusCode": "400" if err else "200",
        "body": err["message"] if err else json.dumps(res),
        "headers": {"Content-Type": "application/json"},
    }

## Version to launch an instance
class Submission_Launch():
    """ Collection of data for a single request to process a dataset """

    def __init__(self, bucket_name, key):
        """ """

        # Get Upload Location Information
        self.bucket_name = bucket_name
        self.path = os.path.join(*key.split('/')[:-2])
        self.logger = utilsparam.s3.Logger(self.bucket_name, self.path)
        #self.out_path = utilsparam.s3.mkdir(self.bucket_name, self.path, config.OUTDIR)
        #self.in_path = utilsparam.s3.mkdir(self.bucket_name, self.path, config.INDIR)

        # Load Content Of Submit File 
        submit_config = utilsparam.s3.load_json(bucket_name, key)
        self.instance_type = submit_config['instance_type'] # TODO default option from config
        self.data_filename = submit_config['filename'] # TODO validate extensions & check existence

    def acquire_instance(self):
        """ Acquires & Starts New EC2 Instance Of The Requested Type & AMI"""
        self.instance = utilsparam.ec2.launch_new_instance(
            instance_type=self.instance_type, 
            ami=os.environ['AMI'],
            logger=self.logger
        )

    def start_instance(self):
        utilsparam.ec2.start_instance_if_stopped(
            instance=self.instance,
            logger=self.logger
        )

    def process_inputs(self):
        """ Initiates Processing On Previously Acquired EC2 Instance """
        print(self.bucket_name,'bucket name')
        print(self.data_filename,'filename')
        print(os.environ['OUTDIR'],'outdir')
        print(os.environ['COMMAND'],'command')
        self.logger.append("Sending command: {}".format(
            os.environ['COMMAND'].format(
                self.bucket_name, self.data_filename, os.environ['OUTDIR']
            )
        ))
        response = utilsparam.ssm.execute_commands_on_linux_instances(
            commands=[os.environ['COMMAND'].format(
                self.bucket_name, self.data_filename, os.environ['OUTDIR']
            )], # TODO: variable outdir as option
            instance_ids=[self.instance.instance_id],
            working_dirs=[os.environ['WORKING_DIRECTORY']],
            log_bucket_name=self.bucket_name,
            log_path=self.logger.path
        )
    ## Declare rules to monitor the states of these instances.  
    def put_instance_monitor_rule(self): 
        self.logger.append('Setting up monitoring on instance')
        ## First declare a monitoring rule for this instance: 
        ruledata,rulename = utilsparam.events.put_instance_rule(self.instance.instance_id)
        arn = ruledata['RuleArn']
        ## Now attach it to the given target
        targetdata = utilsparam.events.put_instance_target(rulename) 


class Submission_Launch_folder(Submission_Launch):
    """
    Generalization of Submission_Launch to a folder. Will launch a separate instance for each file in the bucket. Can be used to replace Submission_Launch whole-hog, as giving the path to the file will still work with this implementation.     
    """
    def __init__(self, bucket_name, key):
        print("using submission_launch_partial")
        # Get Upload Location Information
        self.bucket_name = bucket_name
        self.path = os.path.join(*key.split('/')[:-2])
        self.logger = utilsparam.s3.Logger(self.bucket_name, self.path)
        #self.out_path = utilsparam.s3.mkdir(self.bucket_name, self.path, config.OUTDIR)
        #self.in_path = utilsparam.s3.mkdir(self.bucket_name, self.path, config.INDIR)

        # Load Content Of Submit File 
        submit_file = utilsparam.s3.load_json(bucket_name, key)
        ## Check what instance we should use. 
        try:
            self.instance_type = submit_file['instance_type'] # TODO default option from config
        except KeyError as ke: 
            msg = "DEFAULT: Instance type {} does not exist, using default from config file".format(ke)
            print(msg)
            ## Log this message.
            self.logger.append(msg)
            self.logger.write()

        ## These next two check that the submit file is correctly formatted
        ## Check that we have a dataname field:
        submit_errmsg = "INPUT ERROR: Submit file does not contain field {}, needed to analyze data."
        try: 
            self.data_name = submit_file['dataname'] # TODO validate extensions & check existence
        except KeyError as ke:

            print(submit_errmsg.format(ke))
            ## Write to logger
            self.logger.append(submit_errmsg.format(ke))
            self.logger.write()
            ## Now raise an exception to halt processing, because this is a catastrophic error.  
            raise ValueError("Missing data name to analyze")
    def acquire_instance(self):
        """ Acquires & Starts New EC2 Instances Of The Requested Type & AMI"""
        instances = []
        nb_instances = len(self.filenames)

        ## Check how many instances are running. 
        active = utilsparam.ec2.count_active_instances(self.instance_type)
        ## Ensure that we have enough bandwidth to support this request:
        if active +nb_instances < int(os.environ['DEPLOY_LIMIT']):
            pass
        else:
            self.logger.append("RESOURCE ERROR: Instance requests greater than pipeline bandwidth. Please contact NCAP administrator.")
        

        for i in range(nb_instances):
            instance = utilsparam.ec2.launch_new_instance(
            instance_type=self.instance_type, 
            ami=os.environ['AMI'],
            logger=self.logger
            )
            instances.append(instance)
        self.instances = instances

    def start_instance(self):
        """ Starts new instances if stopped. We write a special loop for this one because we only need a single 60 second pause for all the intances, not one for each in serial"""
        utilsparam.ec2.start_instances_if_stopped(
            instances=self.instances,
            logger=self.logger
        )

    ## Declare rules to monitor the states of these instances.  
    def put_instance_monitor_rule(self): 
        """ For multiple datasets."""
        for instance in self.instances:
            self.logger.append('Setting up monitoring on instance '+str(instance))
            ## First declare a monitoring rule for this instance: 
            ruledata,rulename = utilsparam.events.put_instance_rule(instance.instance_id)
            arn = ruledata['RuleArn']
            ## Now attach it to the given target
            targetdata = utilsparam.events.put_instance_target(rulename) 

    def process_inputs(self):
        """ Initiates Processing On Previously Acquired EC2 Instance """
        print(self.bucket_name,'bucket name')
        print(self.filenames,'filenames')
        print(os.environ['OUTDIR'],'outdir')
        print(os.environ['COMMAND'],'command')
        ## Should we vectorize the log here? 
        [self.logger.append("Sending command: {}".format(
            os.environ['COMMAND'].format(
                self.bucket_name, filename, os.environ['OUTDIR']
            )
        )) for filename in self.filenames]
        print([os.environ['COMMAND'].format(
              self.bucket_name, filename, os.environ['OUTDIR']
              ) for filename in self.filenames],"command send")
        for f,filename in enumerate(self.filenames):
            response = utilsparam.ssm.execute_commands_on_linux_instances(
                commands=[os.environ['COMMAND'].format(
                    self.bucket_name, filename, os.environ['OUTDIR']
                    )], # TODO: variable outdir as option
                instance_ids=[self.instances[f].instance_id],
                working_dirs=[os.environ['WORKING_DIRECTORY']],
                log_bucket_name=self.bucket_name,
                log_path=self.logger.path
                )
            
class Submission_Launch_full(Submission_Launch_folder):
    """
    Final generalization of the Submission_Launch object to work with folders and actively take a configuration file to be passed from the submit file, as opposed to being hacked in from the instance end without interactivity.   
    
    """
    def __init__(self, bucket_name, key):

        # Get Upload Location Information
        self.bucket_name = bucket_name
        self.path = re.findall('.+?(?=/'+os.environ["INDIR"]+')',key)[0] 
        print(self.path,'path')
        self.logger = utilsparam.s3.Logger(self.bucket_name, self.path)
        #self.out_path = utilsparam.s3.mkdir(self.bucket_name, self.path, config.OUTDIR)
        #self.in_path = utilsparam.s3.mkdir(self.bucket_name, self.path, config.INDIR)

        # Load Content Of Submit File 
        submit_file = utilsparam.s3.load_json(bucket_name, key)
        ## Check what instance we should use. 
        try:
            self.instance_type = submit_file['instance_type'] # TODO default option from config
        except KeyError as ke: 
            msg = "Instance type {} does not exist, using default from config file".format(ke)
            print(msg)
            ## Log this message.
            self.logger.append(msg)
            self.logger.write()

        ## These next two check that the submit file is correctly formatted
        ## Check that we have a dataname field:
        submit_errmsg = "Submit file does not contain field {}, needed to analyze data."
        try: 
            self.data_name = submit_file['dataname'] # TODO validate extensions & check existence
        except KeyError as ke:

            print(submit_errmsg.format(ke))
            ## Write to logger
            self.logger.append(submit_errmsg.format(ke))
            self.logger.write()
            ## Now raise an exception to halt processing, because this is a catastrophic error.  
            raise ValueError("Missing data name to analyze")

        try:
            self.config_name = submit_file["configname"] ## TODO
        except KeyError as ke:
            print(submit_errmsg.format(ke))
            ## Write to logger
            self.logger.append(submit_errmsg.format(ke))
            self.logger.write()
            ## Now raise an exception to halt processing, because this is a catastrophic error.  
            raise ValueError(os.environ["MISSING_CONFIG_ERROR"])

        ## Check that we have the actual data in the bucket.  
        exists_errmsg = "S3 Bucket does not contain {}"
        if not utilsparam.s3.exists(self.bucket_name,self.data_name): 
            msg = exists_errmsg.format(self.data_name)
            self.logger.append(msg)
            self.logger.write()
            raise ValueError("dataname given does not exist in bucket.")
        elif not utilsparam.s3.exists(self.bucket_name,self.config_name): 
            msg = exists_errmsg.format(self.config_name)
            self.logger.append(msg)
            self.logger.write()
            raise ValueError("configname given does not exist in bucket.")

        ## Now get the actual paths to relevant data from the foldername: 
        self.filenames = utilsparam.s3.extract_files(self.bucket_name,self.data_name,ext = None) 
        assert len(self.filenames) > 0, "we must have data to analyze."

    def process_inputs(self):
        """ Initiates Processing On Previously Acquired EC3 Instance. This version requires that you include a config (fourth) argument """
        print(self.bucket_name,'bucket name')
        print(self.filenames,'filenames')
        print(os.environ['OUTDIR'],'outdir')
        print(os.environ['COMMAND'],'command')
        try: 
            os.environ['COMMAND'].format("a","b","c","d")
        except IndexError as ie:
            msg = "not enough arguments in the COMMAND argument."
            self.logger.append(msg)
            self.logger.write()
            raise ValueError("Not the correct format for arguments.")

        ## Should we vectorize the log here? 
        [self.logger.append("Sending command: {}".format(
            os.environ['COMMAND'].format(
                self.bucket_name, filename, os.environ['OUTDIR'], self.config_name
            )
        )) for filename in self.filenames]
        print([os.environ['COMMAND'].format(
              self.bucket_name, filename, os.environ['OUTDIR'], self.config_name
              ) for filename in self.filenames],"command send")
        for f,filename in enumerate(self.filenames):
            response = utilsparam.ssm.execute_commands_on_linux_instances(
                commands=[os.environ['COMMAND'].format(
                    self.bucket_name, filename, os.environ['OUTDIR'], self.config_name
                    )], # TODO: variable outdir as option
                instance_ids=[self.instances[f].instance_id],
                working_dirs=[os.environ['WORKING_DIRECTORY']],
                log_bucket_name=self.bucket_name,
                log_path=self.logger.path
                )

class Submission_Launch_log(Submission_Launch_full):
    """
    Latest modification (10/26) to submit framework: instead of dumping straight into the results pile, puts results into a subfolder indexed by the job submission time. 
    """
    def __init__(self,bucket_name,key,time):
        ## Initialize as before:
        # Get Upload Location Information
        self.bucket_name = bucket_name
        ## Get directory above the input directory. 
        self.path = re.findall('.+?(?=/'+os.environ["INDIR"]+')',key)[0] 
        ## Now add in the time parameter: 
        self.time = time
        ## We will index by the submit file name prefix if it exists: 
        submit_search = re.findall('.+?(?=/submit.json)',os.path.basename(key))
        try:
            submit_name = submit_search[0]
        except IndexError as e:
            ## If the filename is just "submit.json, we just don't append anything to the job name. "
            submit_name = ""
            
        ## Now we're going to get the path to the results directory: 
        self.jobname = "job"+submit_name+self.time
        jobpath = os.path.join(self.path,os.environ['OUTDIR'],self.jobname)
        self.jobpath = jobpath
        create_jobdir  = utilsparam.s3.mkdir(self.bucket_name, os.path.join(self.path,os.environ['OUTDIR']),self.jobname)
        
        print(self.path,'path')
        self.logger = utilsparam.s3.JobLogger(self.bucket_name, self.jobpath)
        #self.out_path = utilsparam.s3.mkdir(self.bucket_name, self.path, config.OUTDIR)
        #self.in_path = utilsparam.s3.mkdir(self.bucket_name, self.path, config.INDIR)

        # Load Content Of Submit File 
        submit_file = utilsparam.s3.load_json(bucket_name, key)
        ## Check what instance we should use. 
        try:
            self.instance_type = submit_file['instance_type'] # TODO default option from config
        except KeyError as ke: 
            msg = "Instance type {} does not exist, using default from config file".format(ke)
            print(msg)
            ## Log this message.
            self.logger.append(msg)
            self.logger.write()

        ## These next two check that the submit file is correctly formatted
        ## Check that we have a dataname field:
        submit_errmsg = "INPUT ERROR: Submit file does not contain field {}, needed to analyze data."
        try: 
            self.data_name = submit_file['dataname'] # TODO validate extensions 
        except KeyError as ke:

            print(submit_errmsg.format(ke))
            ## Write to logger
            self.logger.append(submit_errmsg.format(ke))
            self.logger.write()
            ## Now raise an exception to halt processing, because this is a catastrophic error.  
            raise ValueError("Missing data name to analyze")

        try:
            self.config_name = submit_file["configname"] 
            self.logger.assign_config(self.config_name)
        except KeyError as ke:
            print(submit_errmsg.format(ke))
            ## Write to logger
            self.logger.append(submit_errmsg.format(ke))
            self.logger.write()
            ## Now raise an exception to halt processing, because this is a catastrophic error.  
            raise ValueError(os.environ["MISSING_CONFIG_ERROR"])

        ## Check that we have the actual data in the bucket.  
        exists_errmsg = "INPUT ERROR: S3 Bucket does not contain {}"
        if not utilsparam.s3.exists(self.bucket_name,self.data_name): 
            msg = exists_errmsg.format(self.data_name)
            self.logger.append(msg)
            self.logger.write()
            raise ValueError("dataname given does not exist in bucket.")
        elif not utilsparam.s3.exists(self.bucket_name,self.config_name): 
            msg = exists_errmsg.format(self.config_name)
            self.logger.append(msg)
            self.logger.write()
            raise ValueError("configname given does not exist in bucket.")
        ###########################

        ## Now get the actual paths to relevant data from the foldername: 

        self.filenames = utilsparam.s3.extract_files(self.bucket_name,self.data_name,ext = None) 
        assert len(self.filenames) > 0, "we must have data to analyze."

    def process_inputs(self):
        """ Initiates Processing On Previously Acquired EC2 Instance. This version requires that you include a config (fourth) argument """
        print(self.bucket_name,'bucket name')
        print(self.filenames,'filenames')
        print(os.environ['OUTDIR'],'outdir')
        print(os.environ['COMMAND'],'command')
        try: 
            os.environ['COMMAND'].format("a","b","c","d")
        except IndexError as ie:
            msg = "not enough arguments in the COMMAND argument."
            self.logger.append(msg)
            self.logger.write()
            raise ValueError("Not the correct format for arguments.")
     

        ## Should we vectorize the log here? 
        outpath_full = os.path.join(os.environ['OUTDIR'],self.jobname)
        [self.logger.append("Sending command: {}".format(
            os.environ['COMMAND'].format(
                self.bucket_name, filename, outpath_full, self.config_name
            )
        )) for filename in self.filenames]
        print([os.environ['COMMAND'].format(
              self.bucket_name, filename, outpath_full, self.config_name
              ) for filename in self.filenames],"command send")
        for f,filename in enumerate(self.filenames):
            response = utilsparam.ssm.execute_commands_on_linux_instances(
                commands=[os.environ['COMMAND'].format(
                    self.bucket_name, filename, outpath_full, self.config_name
                    )], # TODO: variable outdir as option
                instance_ids=[self.instances[f].instance_id],
                working_dirs=[os.environ['WORKING_DIRECTORY']],
                log_bucket_name=self.bucket_name,
                log_path=os.path.join(self.jobpath,'internal_ec2_logs')
                )
            self.logger.initialize_datasets(filename,self.instances[f].instance_id,response["Command"]["CommandId"])

class Submission_Launch_log_dev(Submission_Launch_full):
    """
    Latest modification (11/1) to submit framework: spawn individual log files for each dataset. . 
    """
    def __init__(self,bucket_name,key,time):
        ## Initialize as before:
        # Get Upload Location Information
        self.bucket_name = bucket_name
        ## Get directory above the input directory. 
        self.path = re.findall('.+?(?=/'+os.environ["INDIR"]+')',key)[0] 
        ## Now add in the time parameter: 
        self.time = time
        ## We will index by the submit file name prefix if it exists: 
        submit_search = re.findall('.+?(?=/submit.json)',os.path.basename(key))
        try:
            submit_name = submit_search[0]
        except IndexError as e:
            ## If the filename is just "submit.json, we just don't append anything to the job name. "
            submit_name = ""
            
        ## Now we're going to get the path to the results directory: 
        self.jobname = "job"+submit_name+self.time
        jobpath = os.path.join(self.path,os.environ['OUTDIR'],self.jobname)
        self.jobpath = jobpath
        create_jobdir  = utilsparam.s3.mkdir(self.bucket_name, os.path.join(self.path,os.environ['OUTDIR']),self.jobname)
        
        print(self.path,'path')
        self.logger = utilsparam.s3.JobLogger(self.bucket_name, self.jobpath)
        #self.out_path = utilsparam.s3.mkdir(self.bucket_name, self.path, config.OUTDIR)
        #self.in_path = utilsparam.s3.mkdir(self.bucket_name, self.path, config.INDIR)

        # Load Content Of Submit File 
        submit_file = utilsparam.s3.load_json(bucket_name, key)
        ## Check what instance we should use. 
        try:
            self.instance_type = submit_file['instance_type'] # TODO default option from config
        except KeyError as ke: 
            msg = "Instance type {} does not exist, using default from config file".format(ke)
            self.instance_type = os.environ["INSTANCE_TYPE"]
            ## Log this message.
            self.logger.append(msg)
            self.logger.write()

        ## These next two check that the submit file is correctly formatted
        ## Check that we have a dataname field:
        submit_errmsg = "INPUT ERROR: Submit file does not contain field {}, needed to analyze data."
        try: 
            self.data_name = submit_file['dataname'] # TODO validate extensions 
        except KeyError as ke:

            print(submit_errmsg.format(ke))
            ## Write to logger
            self.logger.append(submit_errmsg.format(ke))
            self.logger.write()
            ## Now raise an exception to halt processing, because this is a catastrophic error.  
            raise ValueError("Missing data name to analyze")

        try:
            self.config_name = submit_file["configname"] 
            self.logger.assign_config(self.config_name)
        except KeyError as ke:
            print(submit_errmsg.format(ke))
            ## Write to logger
            self.logger.append(submit_errmsg.format(ke))
            self.logger.write()
            ## Now raise an exception to halt processing, because this is a catastrophic error.  
            raise ValueError(os.environ["MISSING_CONFIG_ERROR"])

        ## Check that we have the actual data in the bucket.  
        exists_errmsg = "INPUT ERROR: S3 Bucket does not contain {}"
        if not utilsparam.s3.exists(self.bucket_name,self.data_name): 
            msg = exists_errmsg.format(self.data_name)
            self.logger.append(msg)
            self.logger.write()
            raise ValueError("dataname given does not exist in bucket.")
        elif not utilsparam.s3.exists(self.bucket_name,self.config_name): 
            msg = exists_errmsg.format(self.config_name)
            self.logger.append(msg)
            self.logger.write()
            raise ValueError("configname given does not exist in bucket.")
        ###########################

        ## Now get the actual paths to relevant data from the foldername: 

        self.filenames = utilsparam.s3.extract_files(self.bucket_name,self.data_name,ext = None) 
        assert len(self.filenames) > 0, "we must have data to analyze."

    def process_inputs(self):
        """ Initiates Processing On Previously Acquired EC2 Instance. This version requires that you include a config (fourth) argument """
        print(self.bucket_name,'bucket name')
        print(self.filenames,'filenames')
        print(os.environ['OUTDIR'],'outdir')
        print(os.environ['COMMAND'],'command')
        try: 
            os.environ['COMMAND'].format("a","b","c","d")
        except IndexError as ie:
            msg = "not enough arguments in the COMMAND argument."
            self.logger.append(msg)
            self.logger.write()
            raise ValueError("Not the correct format for arguments.")
     

        ## Should we vectorize the log here? 
        outpath_full = os.path.join(os.environ['OUTDIR'],self.jobname)
        [self.logger.append("Sending command: {}".format(
            os.environ['COMMAND'].format(
                self.bucket_name, filename, outpath_full, self.config_name
            )
        )) for filename in self.filenames]
        print([os.environ['COMMAND'].format(
              self.bucket_name, filename, outpath_full, self.config_name
              ) for filename in self.filenames],"command send")
        for f,filename in enumerate(self.filenames):
            response = utilsparam.ssm.execute_commands_on_linux_instances(
                commands=[os.environ['COMMAND'].format(
                    self.bucket_name, filename, outpath_full, self.config_name
                    )], # TODO: variable outdir as option
                instance_ids=[self.instances[f].instance_id],
                working_dirs=[os.environ['WORKING_DIRECTORY']],
                log_bucket_name=self.bucket_name,
                log_path=os.path.join(self.jobpath,'internal_ec2_logs')
                )
            self.logger.initialize_datasets_dev(filename,self.instances[f].instance_id,response["Command"]["CommandId"])

class Submission_Start_Stack():
    ## Submission class for the case where the instances are being started.
    def __init__(self,bucket_name,key):
        # Get Upload Location Information
        self.bucket_name = bucket_name
        self.path = os.path.join(*key.split('/')[:-2])
        print('logging at '+self.path)
        self.logger = utilsparam.s3.Logger(self.bucket_name, self.path)
        #self.out_path = utilsparam.s3.mkdir(self.bucket_name, self.path, config.OUTDIR)
        #self.in_path = utilsparam.s3.mkdir(self.bucket_name, self.path, config.INDIR)
        
        # Load Content Of Submit File
        submit_config = utilsparam.s3.load_json(bucket_name, key)
        self.instance_id = submit_config['instance_id'] # TODO default option from config
        self.data_filename = submit_config['filename'] # TODO validate extensions & check existence
        
    def acquire_instance(self):
        self.instance = utilsparam.ec2.get_instance(self.instance_id,self.logger)

    def start_instance(self):
        utilsparam.ec2.start_instance_if_stopped(
            instance=self.instance,
            logger=self.logger
        )

    def process_inputs(self):
        """ Initiates Processing On Previously Acquired EC2 Instance """
        print(self.bucket_name,'bucket name')
        print(self.data_filename,'filename')
        print(os.environ['OUTDIR'],'outdir')
        self.logger.append("Sending command: {}".format(
            os.environ['COMMAND'].format(
                self.bucket_name, self.data_filename, os.environ['OUTDIR']
            )
        ))
        response = utilsparam.ssm.execute_commands_on_linux_instances(
            commands=[os.environ['COMMAND'].format(
                self.bucket_name, self.data_filename, os.environ['OUTDIR']
            )], # TODO: variable outdir as option
            instance_ids=[self.instance.instance_id],
            working_dirs=[os.environ['WORKING_DIRECTORY']],
            log_bucket_name=self.bucket_name,
            log_path=self.logger.path
        )
    ## Declare rules to monitor the states of these instances.  
    def put_instance_monitor_rule(self): 
        self.logger.append('Setting up monitoring on instance')
        ## First declare a monitoring rule for this instance: 
        ruledata,rulename = utilsparam.events.put_instance_rule(self.instance.instance_id)
        arn = ruledata['RuleArn']
        ## Now attach it to the given target
        targetdata = utilsparam.events.put_instance_target(rulename) 

def process_upload(bucket_name, key):
    """ Given an upload key and bucket name, determine & take appropriate action
    key: absolute path to created object within bucket.
    bucket: name of the bucket within which the upload occurred.
    """
    ## Conditionals for different deploy configurations: 
    ## First check if we are launching a new instance or starting an existing one. 
    ## NOTE: IN LAMBDA,  JSON BOOLEANS ARE CONVERTED TO STRING
    if os.environ['LAUNCH'] == 'true':
        ## Now check how many datasets we have
        submission = Submission_Launch_folder(bucket_name, key)
    elif os.environ["LAUNCH"] == 'false':
        submission = Submission_Start_Stack(bucket_name, key)
    print("acquiring")
    submission.acquire_instance()
    print('writing0')
    submission.logger.write()
    ## NOTE: IN LAMBDA,  JSON BOOLEANS ARE CONVERTED TO STRING
    if os.environ["MONITOR"] == "true":
        print('setting up monitor')
        submission.put_instance_monitor_rule()
    elif os.environ["MONITOR"] == "false":
        print("skipping monitor")
    print('writing1')
    submission.logger.write()
    print('starting')
    submission.start_instance()
    print('writing2')
    print('sending')
    submission.process_inputs()
    print("writing3")
    submission.logger.write()

def handler(event, context):
    """ Handler that get called by lambda whenever an event occurs. """
    for record in event['Records']:
        bucket_name = record['s3']['bucket']['name']
        key = record['s3']['object']['key']
        print("handler_params",bucket_name,key)
        process_upload(bucket_name, key);


def process_upload_full(bucket_name, key):
    """ 
    Updated version that can handle config files. 
    Inputs:
    key: absolute path to created object within bucket.
    bucket: name of the bucket within which the upload occurred.
    """
    ## Conditionals for different deploy configurations: 
    ## First check if we are launching a new instance or starting an existing one. 
    ## NOTE: IN LAMBDA,  JSON BOOLEANS ARE CONVERTED TO STRING
    if os.environ['LAUNCH'] == 'true':
        ## Now check how many datasets we have
        submission = Submission_Launch_full(bucket_name, key)
    elif os.environ["LAUNCH"] == 'false':
        raise NotImplementedError("This option not available for configs. ")
    print("acquiring")
    submission.acquire_instance()
    print('writing0')
    submission.logger.write()
    ## NOTE: IN LAMBDA,  JSON BOOLEANS ARE CONVERTED TO STRING
    if os.environ["MONITOR"] == "true":
        print('setting up monitor')
        submission.put_instance_monitor_rule()
    elif os.environ["MONITOR"] == "false":
        print("skipping monitor")
    print('writing1')
    submission.logger.write()
    print('starting')
    submission.start_instance()
    print('writing2')
    print('sending')
    submission.process_inputs()
    print("writing3")
    submission.logger.write()

def handler_full(event, context):
    """ Handler that get called by lambda whenever an event occurs. Updated version that can handle config files. """
    for record in event['Records']:
        bucket_name = record['s3']['bucket']['name']
        key = record['s3']['object']['key']
        print("handler_params",bucket_name,key)
        print(event,context,'event, context')
        process_upload_full(bucket_name, key);

def process_upload_log(bucket_name, key,time):
    """ 
    Updated version that can handle config files. 
    Inputs:
    key: absolute path to created object within bucket.
    bucket: name of the bucket within which the upload occurred.
    time: the time at which the upload event happened. 
    """

    ## Conditionals for different deploy configurations: 
    ## First check if we are launching a new instance or starting an existing one. 
    ## NOTE: IN LAMBDA,  JSON BOOLEANS ARE CONVERTED TO STRING
    if os.environ['LAUNCH'] == 'true':
        ## Now check how many datasets we have
        submission = Submission_Launch_log(bucket_name, key, time)
    elif os.environ["LAUNCH"] == 'false':
        raise NotImplementedError("This option not available for configs. ")
    print("acquiring")
    submission.acquire_instance()
    print('writing0')
    submission.logger.write()
    ## NOTE: IN LAMBDA,  JSON BOOLEANS ARE CONVERTED TO STRING
    if os.environ["MONITOR"] == "true":
        print('setting up monitor')
        submission.put_instance_monitor_rule()
    elif os.environ["MONITOR"] == "false":
        print("skipping monitor")
    print('writing1')
    submission.logger.write()
    print('starting')
    submission.start_instance()
    print('writing2')
    print('sending')
    submission.process_inputs()
    print("writing3")
    submission.logger.write()

def process_upload_log_dev(bucket_name, key,time):
    """ 
    Updated version that can handle config files. 
    Inputs:
    key: absolute path to created object within bucket.
    bucket: name of the bucket within which the upload occurred.
    time: the time at which the upload event happened. 
    """

    ## Conditionals for different deploy configurations: 
    ## First check if we are launching a new instance or starting an existing one. 
    ## NOTE: IN LAMBDA,  JSON BOOLEANS ARE CONVERTED TO STRING
    if os.environ['LAUNCH'] == 'true':
        ## Now check how many datasets we have
        submission = Submission_Launch_log_dev(bucket_name, key, time)
    elif os.environ["LAUNCH"] == 'false':
        raise NotImplementedError("This option not available for configs. ")
    print("acquiring")
    submission.acquire_instance()
    print('writing0')
    submission.logger.write()
    ## NOTE: IN LAMBDA,  JSON BOOLEANS ARE CONVERTED TO STRING
    if os.environ["MONITOR"] == "true":
        print('setting up monitor')
        submission.put_instance_monitor_rule()
    elif os.environ["MONITOR"] == "false":
        print("skipping monitor")
    print('writing1')
    submission.logger.write()
    print('starting')
    submission.start_instance()
    print('writing2')
    print('sending')
    submission.process_inputs()
    print("writing3")
    submission.logger.write()

def handler_log(event,context):
    """
    Newest version of handler that logs outputs to a subfolder of the result folder that is indexed by the job submission date and the submit name.
    """
    
    for record in event['Records']:
        time = record['eventTime']
        bucket_name = record['s3']['bucket']['name']
        key = record['s3']['object']['key']
        print("handler_params",bucket_name,key,time)
        print(event,context,'event, context')
        process_upload_log(bucket_name, key, time);

def handler_log_dev(event,context):
    """
    Newest version of handler that logs outputs to a subfolder of the result folder that is indexed by the job submission date and the submit name.
    """
    
    for record in event['Records']:
        time = record['eventTime']
        bucket_name = record['s3']['bucket']['name']
        key = record['s3']['object']['key']
        print("handler_params",bucket_name,key,time)
        print(event,context,'event, context')
        process_upload_log_dev(bucket_name, key, time);

