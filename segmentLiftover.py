import click
import sys
import pandas as pd
import math
import subprocess
import logging
import os
import re
from datetime import datetime



##########################################################################
#
#                   Initialize globals
#
##########################################################################
startTime = datetime.now()

log_dir = 'logs/'
chain_dir = 'chains/'
liftover_path = './liftOver'
#chain_dir = os.path.abspath('./chains')

# stores remapped positions for fast re-access
# key = chro_pos, value = [chro, pos, flat=mapped/unmapped]
remapped_list = {}

# stores processed files, used for restore progress
file_list = []

# Valid chromosome names
valid_chro_names = ['chr'+str(i) for i in range(1,23)]
valid_chro_names.append('chrX')
valid_chro_names.append('chrY')

# the distance to next remapp position
step_size = 500

# the number positions to search
steps = 4000

# create a directory for temp files, this dir is hard coded.
os.makedirs('./tmp', exist_ok=True)

################### loggers ###################

# check directory existance
os.makedirs(log_dir, exist_ok=True)

# increamental log names
log_suffix = ''
if os.path.isfile('logs/liftover.log'):
    suf_index = 2
    while True:
        logfile_name = 'logs/liftover_' + str(suf_index) + '.log'
        if os.path.isfile(logfile_name):
            suf_index += 1
        else:
            log_suffix = '_' + str(suf_index)
            break
    

# system logger 
logger = logging.getLogger('liftover')
handler = logging.FileHandler(os.path.join(log_dir, 'liftover{}.log'.format(log_suffix)), mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# prgress logger, records processed files, used for restore.
progress_logger = logging.getLogger('progress')
handler = logging.FileHandler(os.path.join(log_dir,'progress{}.log'.format(log_suffix)), mode='w')
handler.setFormatter(logging.Formatter('%(message)s'))
progress_logger.setLevel(logging.INFO)
progress_logger.addHandler(handler)

# unmapped positions logger, records segments that's not properly lifted.
unmapped_logger = logging.getLogger('unmapped')
handler = logging.FileHandler(os.path.join(log_dir,'unmapped{}.log'.format(log_suffix)), mode='w')
handler.setFormatter(logging.Formatter('%(message)s'))
unmapped_logger.setLevel(logging.INFO)
unmapped_logger.addHandler(handler)
unmapped_logger.info('{}\t{}\t{}\t{}\t{}\t{}'.format('chromosome','start','end','same_chr','length_ratio','file'))


################### stat counters ###################
total_seg = 0
lifted_seg = 0
remapped_seg =0
rejected_seg = 0
unmapped_seg = 0

total_pro = 0
lifted_pro = 0
remapped_pro = 0
rejected_pro = 0
unmapped_pro = 0




# file = '/Volumes/arraymapMirror/arraymap/hg18/19197950/19197950_MB66_6332/segments.tab'
# ch = 'hg18ToHg19.over.chain'


# input_dir = '/Volumes/arraymapMirror/arraymap/hg18/GSE49'
# input_dir = '/Volumes/arraymapMirror/arraymap/hg18/GSE1755'
# output_dir = '/Users/bogao/DataFiles/hg19'
# segments_files = []
# probes_files = []


##########################################################################
#
#                   Utility functions
#
##########################################################################

# Map the unmapped positions to their nearest mappable positions
#
# Param:
# fin: path of the unmapped file generated by liftover
# chain: path of the chain file, should be same as used by liftover
# remap: the remapped_list
#
# Use global params:
# steps, step_size 
# the searching range is 100(bps)/step_stize * steps in both direction.
#
# Return:
# a list of lists with chro, new_pos, name 
# -1 in exception
#
# Note: unmappable positions will be returned with value 0
def solveUnmappables(fin, chain, remap):
    
    try:
        logger = logging.getLogger('liftover')
        
        # read in unmapped file
        df = pd.read_table(fin, sep='\t', comment='#', header=None, names=['chro','start','end','name'])
        df.loc[df.chro == 'chr23', 'chro'] = 'chrX'
        df.loc[df.chro == 'chr24', 'chro'] = 'chrY'
        # keep new coordinates
        positions = []
        # number of items
        num_pos = df.shape[0]
        counter = 0
        cmd = [liftover_path, './tmp/remap.bed', chain, './tmp/remap_new.bed', 
            './tmp/remap.unmapped']

        
        
        # For each unmapped postion,
        # if it is in the remapped_list, get new position from the list
        # otherwise, gradually search along both sides of the chromesome,
        # until a mappable position is found
        # If nothing is mappable in 20M base range, assume that pos is unmappable.
        for i in range(num_pos):

            chro = df.iloc[i,0]
            start = df.iloc[i,1]
            name = df.iloc[i,3]
            new_pos = -1
            new_chro = 'NA'
            key = '{}_{}'.format(chro, start)

            # use buffered mapping is possible
            if key in remap:
                new_chro = remap[key][0]
                new_pos = remap[key][1]
                flag = remap[key][2]
                if flag == 'mapped':
                    counter += 1
                else:
                    logger.warning('Failed to remap (cached): ' + str([chro, start, name]))
            # do a stepwise mapping
            else:
                with open('./tmp/remap.bed', 'w') as f:
                    for i in range(1,steps):
                        print('{}\t{}\t{}\t{}'.format(chro, start+i*step_size, start+i*step_size+1, name), file=f)
                        print('{}\t{}\t{}\t{}'.format(chro, start-i*step_size, start-i*step_size+1, name), file=f)

                return_info = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                # check running result
                if return_info.returncode != 0 :
                    logger.warning('Remapping failed, cmd error: ' + str([chro, start, name]))
                elif os.path.getsize('./tmp/remap_new.bed') == 0 :
                    logger.warning('Failed to remap (new): ' + str([chro, start, name]))
                    remap[key] = [new_chro, new_pos, 'unmapped']
                # use the first mapping result
                else:
                    with open('./tmp/remap_new.bed', 'r') as f:
                        next(f)
                        for line in f:
                            line = line.split('\t')
                            if len(line) > 1:
                                new_chro = line[0]
                                new_pos = int(line[1])
                            if new_chro == chro:
                                remap[key] = [new_chro, new_pos, 'mapped']
                                counter += 1
                                break




                        # while True:
                        #     line = f.readline()
                        #     line = line.split('\t')
                        #     if len(line) > 1:
                        #         new_chro = line[0]
                        #         new_pos = int(line[1])
                        #         if new_chro == chro:
                        #             remap[key] = [new_chro, new_pos, 'mapped']
                        #             counter += 1
                        #             break


            positions.append([new_chro, new_pos, name])
            
        logger.info('Remapped %i/%i positions.', counter, num_pos)
        return positions
    
    
    except Exception as e:
        logger.exception('Failure in remapping: %s', fin)
        return -1















# Convert the genome coordinates in segments.tab to the specified the edition
# according to the provided chain file.
#
# Params:
# fin: the path of the input file
# chain: the path of the chain file
# remap: the remapped_list
#
# Return: 
# 0 or -1
#
def convertSegments(fin, fo, chain, remap, remap_flag=True, new_colnames = []):
    
    logger = logging.getLogger('liftover')
    logger.info('Processing segment:\t%s', fin)

    try:

        df = pd.read_table(fin, sep='\t', low_memory=False)
        
        # save original column name
        original_colnames = df.columns.values.tolist()
        
        #Rename columns for processing
        df.rename(columns={df.columns[0]:'sample_id', df.columns[1]:'chromosome', df.columns[2]:'start', 
                           df.columns[3]:'stop'}, inplace=True)
       
        #Save column names for order restore after processing.
        col_names = df.columns
        
        #Generate new columns for processing
        df['chr'] = 'chr' + df['chromosome'].astype(str)
        df['name'] = df.index

        #Drop NA
        df = df.dropna(axis=0, how='any')

        #Force positions to be integer
        df.start = df.start.astype(int)
        df.stop = df.stop.astype(int)

        #Filter chromosome names
        df.loc[df.chr == 'chr23', 'chr'] = 'chrX'
        df.loc[df.chr == 'chr24', 'chr'] = 'chrY'
        df = df[df['chr'].isin(valid_chro_names)]
        
        # update global counter
        global total_seg
        this_total = df.shape[0]
        total_seg += this_total

        #Create a file of start coordinates
        df_starts = df.loc[:,['chr','start','stop','name']]
        df_starts['stop'] = df_starts.start + 1
        df_starts.to_csv('./tmp/starts.bed', sep=' ', index=False, header=False)


        #Create a file of end coordinates
        df_ends = df.loc[:,['chr','start','stop','name']]
        df_ends['start'] = df_ends.stop - 1
        df_ends.to_csv('./tmp/ends.bed', sep=' ', index=False, header=False)

    
    
        #Convert the start coordinates
        cmd = [liftover_path , './tmp/starts.bed' , chain , './tmp/starts_new.bed' ,
            './tmp/starts.unmapped']
        return_info = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if return_info.returncode != 0 :
            logger.error('sh: %s', cmd)
            raise RuntimeError(cmd)
            
    
        #Read in the new start positions from a file
        starts_new = pd.read_table('./tmp/starts_new.bed', sep='\t', names=df_starts.columns)
        del starts_new['stop']
        # update counter
#        lifted_start = starts_new.shape[0]
#        remapped_start = 0

        #Remap unmapped start positions
        if (remap_flag == True) and (os.path.getsize('./tmp/starts.unmapped') >0):
            starts_remap = solveUnmappables('./tmp/starts.unmapped', chain, remap)
            starts_remap = pd.DataFrame(starts_remap, columns=starts_new.columns)
            # update counter
#            remapped_start = starts_remap.shape[0]
            #Merge start positions
            starts_new = starts_new.append(starts_remap)
        else: 
            starts_remap = pd.DataFrame(columns=starts_new.columns)
       
        #Convert the end coordinates
        cmd = [liftover_path , './tmp/ends.bed' , chain ,  './tmp/ends_new.bed' ,
            './tmp/ends.unmapped' ]
        return_info = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if return_info.returncode != 0 :
            logger.error('sh: %s', cmd)
            raise RuntimeError(cmd)
    
        #Read in the new end positions from a file
        ends_new = pd.read_table('./tmp/ends_new.bed', sep='\t', names=df_ends.columns)
        del ends_new['start']
        # update counter
#        lifted_end = ends_new.shape[0]
#        remapped_end = 0
        #ends_new.rename(columns={'start':'stop'}, inplace=True)


        #Remap unmapped end positions
        if (remap_flag == True) and (os.path.getsize('./tmp/ends.unmapped') >0):
            ends_remap = solveUnmappables('./tmp/ends.unmapped', chain, remap)
            ends_remap = pd.DataFrame(ends_remap, columns=ends_new.columns)
            # update counter
#            remapped_end = ends_remap.shape[0]
            #Merge end positions
            ends_new = ends_new.append(ends_remap)
        else:
            ends_remap = pd.DataFrame(columns=ends_new.columns)
        
        

        #Merge new positions with original data 
        dd = pd.merge(starts_new,ends_new,how='inner', on=['name'], suffixes=['_s', '_e'])
        
        # update counter
        #lifted = lifted_start if lifted_start < lifted_end else lifted_end
        #remapped = remapped_start if remapped_start > remapped_end else remapped_end
#        global lifted_seg
#        #lifted_seg += lifted
##        unique_lifted = pd.merge(starts_new, ends_new, how='outer', on=['name'])
#        lifted_seg += dd.shape[0]
        
        
        df_new = pd.merge(dd, df, how='left', on=['name'],suffixes=['_new','_old'])
        #df_new.drop(['chr', 'name', 'start_old', 'stop_old'], axis=1, inplace=True)

        
        #Generate new columns for error checking
        df_new['chr_cmp'] = (df_new.chr_s == df_new.chr_e)
        df_new['pos_cmpRatio'] = (df_new.stop_new - df_new.start_new) / (df_new.stop_old - df_new.start_old)
        
        #Check bad liftovers
        df_mis = df_new[    ((df_new.start_new == -1) | (df_new.stop_new == -1)) |
                            (df_new.chr_cmp == False) | 
                            ((df_new.pos_cmpRatio < 0.5) | (df_new.pos_cmpRatio > 2))]

        # update global counter
        global remapped_seg, rejected_seg, unmapped_seg, lifted_seg
        unmapped = df_mis[(df_mis.start_new == -1) | (df_mis.stop_new == -1)].shape[0]
#        remapped_seg = remapped_seg + remapped 
        unmapped_seg += unmapped
        rejected_seg = rejected_seg + df_mis.shape[0] - unmapped
        if remap_flag == True:
            uniqe_remapped = pd.merge(starts_remap, ends_remap, how='outer', on=['name'])
            remapped_seg += uniqe_remapped.shape[0]
        else:
            uniqe_remapped = pd.DataFrame()
        lifted_seg = lifted_seg + this_total - unmapped - uniqe_remapped.shape[0]
        
        #Invoke unmapped logger
        unmapped_logger = logging.getLogger('unmapped')
        #logging unmapped positions
        for index, row in df_mis.iterrows():
            unmapped_logger.info('{}\t{}\t{}\t{}\t{:.4f}\t{}'.format(row['chr'],
                row['start_old'],row['stop_old'],row['chr_cmp'],row['pos_cmpRatio'],fin))
                
        
        #Rename and rearrange columns back to the original order
        df_new = df_new[~df_new.name.isin(df_mis.name)]
        df_new.rename(columns={'start_new':'start', 'stop_new':'stop'}, inplace=True)
        df_new = df_new[col_names]
        
        #restore column names
        if len(new_colnames) > 0:
            df_new.rename(columns={'sample_id':new_colnames[0], 'chromosome':new_colnames[1],
                                   'start':new_colnames[2], 'stop':new_colnames[3]}, inplace=True)
        else:
            df_new.columns = original_colnames
            
        
        os.makedirs(os.path.dirname(fo), exist_ok=True)
        # print(fo)
        df_new.to_csv(fo, sep='\t', index=False, float_format='%.4f') 
                
        logger.info('Finished\n')
        progress_logger = logging.getLogger('progress')
        progress_logger.info(fin)
        return 0
    
    except Exception as e:
        logger.exception('Failure in segment: %s', fin)
        return -1
        
    













# Convert the genome coordinates in CNprobes.tab to the specified the edition
# according to the provided chain file.
#
# Params:
# fin: the path of the input file
# chain: the path of the chain file
# remap: the remapped_list
#
# Return: 
# 0 or -1
#
def convertProbes(fin, fo, chain, remap, remap_flag=True, new_colnames=[]):
    
    logger = logging.getLogger('liftover')
    logger.info('Processing probe:\t%s', fin)
    
    try:
        #col_names=['probeID', 'chro', 'pos', 'value']
        
        
        #Special cases, some files do not have an ID column
        #Will generate ID for the new file.
#         with open(fin, 'r') as f:
#             line = f.readline().split('\t')
#             if len(line) < 4:
                
#                 logger.error('Unknown format: %s', fin)
#                 raise

        #df = pd.read_table(fin, sep='\t', header=0, names=col_names )
        
        
        df = pd.read_table(fin, sep='\t', low_memory=False)
        if df.columns.size < 4:
            df.insert(0, 'probe_id', 'ID_' + df.index.astype(str))
            #df['probe_id'] = 'ID_' + df.index.astype(str)

        # save original column name
        original_colnames = df.columns.values.tolist()        
        
        df.rename(columns={df.columns[0]:'probe_id', df.columns[1]:'chromosome',
                           df.columns[2]:'position'}, inplace=True)        
        
        #Save column names for later restore.
        col_names = df.columns
        
        
        #Generate new columns for processing
        df['chr'] = 'chr' + df['chromosome'].astype(str)
        df['name'] = df.index

        #Drop NA
        df = df.dropna(axis=0, how='any')

        #Force positions to be integer
        df.position = df.position.astype(int)

        #Filter chromosome names
        df.loc[df.chr == 'chr23', 'chr'] = 'chrX'
        df.loc[df.chr == 'chr24', 'chr'] = 'chrY'
        df = df[df['chr'].isin(valid_chro_names)]
        
        # update counter
        global total_pro
        total_pro += df.shape[0]

        #Create a file of probe coordinates
        df_probes = df.loc[:,['chr','position']]
        df_probes['pos1'] = df_probes.position + 1
        df_probes['name'] = df_probes.index
        df_probes.to_csv('./tmp/probes.bed', sep=' ', index=False, header=False)

    
        #Convert the probe coordinates
        cmd = [liftover_path , './tmp/probes.bed' , chain , './tmp/probes_new.bed' ,
            './tmp/probes.unmapped']
        return_info = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if return_info.returncode != 0 :
            logger.error('sh: %s', cmd)
            raise RuntimeError(cmd)
            
    
        #Read in the new probe positions from a file
        probes_new = pd.read_table('./tmp/probes_new.bed', sep='\t', names=df_probes.columns)
        del probes_new['pos1']
        # update counter
        global lifted_pro
        lifted_pro += probes_new[probes_new.position !=-1].shape[0]
        remapped = 0


        #Remap the unmapped
        if (remap_flag == True) and (os.path.getsize('./tmp/probes.unmapped') >0):
            probes_remap = solveUnmappables('./tmp/probes.unmapped', chain, remap)
            probes_remap = pd.DataFrame(probes_remap, columns=probes_new.columns)
            # update counter
            remapped = probes_remap.shape[0]
            #Merage new positions
            probes_new = probes_new.append(probes_remap)
        
        #Merge and rearrange the coloumns to the original format
        df_new = pd.merge(probes_new, df, how='left', on=['name'],suffixes=['_new','_old'])
        
        #Check if new and old positions are on the chromosome
        df_new['chr_cmp'] = (df_new.chr_new == df_new.chr_old)        
        #Check if the new position is unmappable
        #Merge all unmapped positions
        df_mis = df_new[ (df_new.chr_cmp == False) | (df_new.position_new == -1)]
        
        # update global counter
        global remapped_pro, rejected_pro, unmapped_pro
        unmapped = df_mis[df_mis.position_new == -1].shape[0]
        unmapped_pro += unmapped
        remapped_pro = remapped_pro + remapped - unmapped
        rejected_pro = rejected_pro + df_mis.shape[0] - unmapped

        #Invoke unmapped logger
        unmapped_logger = logging.getLogger('unmapped')
        #logging unmapped positions
        for index, row in df_mis.iterrows():
            unmapped_logger.info('{}\t{}\t{}\t{}\t{}\t{}'.format( row['chr_old'],
                row['position_old'], '-1', row['chr_cmp'], '-1', fin))
        
        
        df_new = df_new[~df_new.name.isin(df_mis.name)]
        df_new.rename(columns={'position_new':'position'}, inplace=True)
        df_new = df_new[col_names]
        
        #restore column names
        if len(new_colnames) > 0:
            df_new.rename(columns={'probe_id':new_colnames[0], 'chromosome':new_colnames[1],
                                   'position':new_colnames[2]}, inplace=True)
        else:
            df_new.columns = original_colnames
        
        os.makedirs(os.path.dirname(fo), exist_ok=True)
        df_new.to_csv(fo, sep='\t', index=False, float_format='%.4f') 
        
        logger.info('Finished\n')
        progress_logger = logging.getLogger('progress')
        progress_logger.info(fin)
        return 0
    
    except Exception as e:
        logger.exception('Failure in probe: %s', fin)
        return -1
        






##########################################################################
#
#                   Command line interface
#
##########################################################################

@click.command()
@click.option('-i', '--input_dir', help='The directory to start processing.')
@click.option('-o', '--output_dir', help='The directory to write new files.')
@click.option('-c', '--chain_file', help='Specify the chain file name.')
@click.option('-si', '--segment_input_file', help='Specify the segment input file name.')
@click.option('-so', '--segment_output_file', help='Specify the segment output file name.')
@click.option('-pi', '--probe_input_file', help='Specify the probe input file name.')
@click.option('-po', '--probe_output_file', help='Specify the probe output file name.')
@click.option('-l', '--liftover', 'liftover_path_usr', type=str, help='Specify the location of the UCSC liftover program.')
@click.option('-t', '--test_mode', type=int, help='Only process a limited number of files.')
@click.option('-f', '--file_indexing', is_flag=True, help='Only generate the index file.')
@click.option('-x', '--index_file', type=click.File('r'), help='Specify an index file containing file paths.')
@click.option('-r', '--remap_file', type=click.File('r'), help='Specify an remapping list file.')
@click.option('--step_size', 'step_size_usr', default=400, help='The step size of remapping (in bases, default:400).')
@click.option('--range', 'search_range', default=10, help='The range of remapping search (in kilo bases, default:10).')
@click.option('--no_remapping', is_flag=True, help='No remapping, only original liftover.')
@click.option('--new_segment_header', nargs=4, type=str, help='Specify 4 new column names for new segment files.' )
@click.option('--new_probe_header', nargs=3, type=str, help='Specify 3 new column names for new probe files.')
@click.option('--resume', 'resume_files', nargs=2, type=str, help='Specify a index file and a progress file to resume an interrupted job.')
@click.option('--clean', is_flag=True, help='Clean up log files.')

def cli(input_dir, output_dir, chain_file, clean, test_mode, file_indexing, segment_input_file, segment_output_file, 
        probe_input_file, probe_output_file, step_size_usr, search_range, index_file, remap_file, no_remapping,
        new_segment_header, new_probe_header, resume_files, liftover_path_usr):


    test_counter = 0

    # Clean the log files
    if clean:
        for f in os.listdir(log_dir):
            path = os.path.join(log_dir, f)
            if os.path.isfile(path):
                os.remove(path)
        sys.exit('Log files cleaned up.')


    # Check if the liftOver program exists
    if liftover_path_usr:
        global liftover_path
        liftover_path = liftover_path_usr

    if not os.path.isfile(liftover_path):
        sys.exit('Can not find the UCSC liftover prgram at {}'.format(liftover_path))

    # Check params
#    if ((segment_input_file == None and segment_output_file !=None) or \
#        (segment_input_file != None and segment_output_file ==None)) :
#        sys.exit('Error: Must specify both input & output names for semgent or probe file.') 
#    if ((probe_input_file == None and probe_output_file != None) or \
#        (probe_input_file != None and probe_output_file == None)) :
#        sys.exit('Error: Must specify both input & output names for semgent or probe file.') 
#    if segment_input_file == None and segment_output_file ==None and \
#        probe_input_file == None and probe_output_file == None :
#        sys.exit('Error: Must specify input and output file names.')
    
    #check input & output file 
    if (segment_input_file == None) and (probe_input_file == None):
        sys.exit('Error: Must specify at least one input file name for semgent or probe.') 
        
    if segment_input_file:
        try:
            seg_pattern = re.compile(segment_input_file)
        except re.error:
            sys.exit('{} is not a valid regular expression.'.format(segment_input_file))
        # if segment_output_file == None:
        #     segment_output_file = segment_input_file
    
    if probe_input_file:
        try:
            pro_pattern = re.compile(probe_input_file)
        except re.error:
            sys.exit('{} is not a valid regular expression.'.format(probe_input_file))
        # if probe_output_file == None:
        #     probe_output_file = probe_input_file  
    



    # Validate input_dir
    if input_dir:
        if os.path.isdir(input_dir) == False:
            sys.exit('Error: input direcotry does not exist.')
    else:
        sys.exit('Error: input_dir, out_dir, chain_file and a input file are required. Check --help for more information.')

    if output_dir == None:
        sys.exit('Error: input_dir, out_dir, chain_file and a input file are required. Check --help for more information.')


    # Validate output_dir
    # if output_dir:
    #     if os.path.isdir(output_dir) == False:
    #         sys.exit('Error: output direcotry does not exist.')
    # else:
    #     sys.exit('Error: input_dir, out_dir and genome_editions are required. Check --help for more information.')

    # # Validate genome_editions
    # if not genome_editions:
    #     sys.exit('Error: input_dir, out_dir and genome_editions are required. Check --help for more information.')
    # if genome_editions == '18to19':
    #     chainfile = chainfile_18to19
    # elif genome_editions == '18to38':
    #     chainfile = chainfile_18to38

    default_chains = ['hg18ToHg19', 'hg18ToHg38', 'hg19ToHg38','hg38ToHg19','hg19ToHg18']
    if not chain_file:
        sys.exit('Error: please specify a chain file.')
    elif chain_file in default_chains:
        chain_file = chain_file + '.over.chain.gz'
        chain_file = os.path.join(os.path.dirname(__file__), chain_dir, chain_file )
    if os.path.isfile( chain_file ) == False:
        sys.exit('Error: chainfile does not exist.')


    # Assign step value
    global step_size, steps
    if step_size_usr > 0 :
        step_size = step_size_usr
    else:
        sys.exit('step_size must be greater than 0')
        
    if search_range >0 :
        steps = math.ceil(search_range*1000/step_size)
    else:
        sys.exit('range must be greater than 0')

    # convert no_remapping flg
    remap_flag = not no_remapping


    #########   Print input options   ############
    print('Parameters:')
    print('input_dir: {}'.format(input_dir) )
    print('output_dir: {}'.format(output_dir) )
    print('chain_file: {}'.format(chain_file) )
    print('test_mode: {}'.format(test_mode) )
    print('file_indexing: {}'.format(file_indexing) )
    print('segment_input_file: {}'.format(segment_input_file) )
    print('segment_output_file: {}'.format(segment_output_file) )
    print('probe_input_file: {}'.format(probe_input_file) )
    print('probe_output_file: {}'.format(probe_output_file) )
    print('setp_size: {}'.format(step_size_usr) )
    print('range: {}'.format( search_range ) )
    print('index_file: {}'.format( index_file.name if index_file else index_file ) )
    print('remap_file: {}'.format( remap_file.name if remap_file else remap_file) )
    print('no_remapping: {}'.format( no_remapping ))
    print('new_segment_header: {}'.format( new_segment_header))
    print('new_probe_header: {}'.format( new_probe_header))
    print()




    #########   Indexing all the files to be processed   ############

    #Recover the file list, if fileList.log exists.
    # if os.path.isfile(os.path.join(log_dir, 'fileList.log')):
    #     print('Index file detected, recovered from ./logs/fileList.log')
    #     with open(os.path.join(log_dir, 'fileList.log'), 'r') as fin:
    #         for line in fin:
    #             file_list.append(line.strip())

                # # test mode
                # if test_mode:
                #     test_counter += 1
                #     if test_counter > test_mode:
                #         break

    global file_list
    # resume function
    if len(resume_files) >0:
        if not os.path.isfile(resume_files[0]):
            sys.exit('--resume: index file does not exist.')
        if not os.path.isfile(resume_files[1]):
            sys.exit('--resume: progress file does not exist.')
        resume_index = []
        resume_progress = []
        with open(resume_files[0], 'r') as fi:
            for line in fi:
                resume_index.append(line.strip())
        with open(resume_files[1], 'r') as fi:
            for line in fi:
                resume_progress.append(line.strip())
        file_list = list(set(resume_index) - set(resume_progress))
        print('Resume from previous interruption, {} files processed, {} files to go.'.format(
                len(resume_progress), len(resume_index)-len(resume_progress)))


    elif index_file:
        for line in index_file:
            file_list.append(line.strip())
            # test mode
            if test_mode:
                test_counter += 1
                if test_counter > test_mode:
                    break
        print('Index file detected, recovered from {}'.format(index_file.name))
        print('detected {} files.'.format(len(file_list)))
    #Traverse directories to index all segments.tab and CNprobes.tab files.
    else:
        print('Indexing files to process, this may take some time.')
        seg_counter = 0
        pro_counter = 0
        with click.progressbar(os.walk(input_dir), label='Be patient: ', fill_char=click.style('*', fg='red')) as bar:


            # File traverse
            for root, subdirs, files in bar:
                for f in files:
#                    if (f == segment_input_file):
                    if (segment_input_file !=None) and  (seg_pattern.match(f)):
                        path = os.path.join(root,f)
                        file_list.append(path)
                        seg_counter += 1
#                    elif(f == probe_input_file):
                    elif (probe_input_file !=None) and (pro_pattern.match(f)):
                        path = os.path.join(root,f)
                        file_list.append(path)
                        pro_counter += 1
                
                # test mode
                if test_mode:
                    test_counter += 1
                    if test_counter > test_mode:
                        break

            # Save the file list to disk
            with open(os.path.join(log_dir, 'fileList{}.log'.format(log_suffix)), 'w') as fo:
                for line in file_list:
                    print(line,file=fo)

            # Terminate for file_indexing mode
            if file_indexing:
                sys.exit('Indexing file created.')
        print('detected {} segment files and {} probe files.\n'.format(seg_counter, pro_counter))




    # Recover the remapped_list if possible
    # if os.path.isfile('./logs/remapped.log'):
    #     with open('./logs/remapped.log', 'r') as fi:
    #         for line in fi:
    #             line = line.strip().split('\n')
    #             key = line[0]
    #             chro = line[1]
    #             pos = int(line[2])
    #             flag = line[3]
    #             remapped_list[key] = [chro, pos, flag]
    #     print('Remapped positions detected, recovered from ./logs/remapped.log')

    if remap_file:
        if len(next(remap_file).split('\t')) != 4:
            sys.exit('Wrong remap file.')
        for line in remap_file:
            line = line.strip().split('\t')
            key = line[0]
            chro = line[1]
            pos = int(line[2])
            flag = line[3]
            remapped_list[key] = [chro, pos, flag]
        print('Remapped positions detected, recovered from {}'.format(remap_file.name))

        










    # counters for display
    seg_succ_counter = 0
    seg_fail_counter = 0
    pro_succ_counter = 0
    pro_fail_counter = 0
    #########   Liftover   ############
    with click.progressbar(file_list, label='Lifting: ', fill_char=click.style('*', fg='green')) as bar:

        
        for f in bar:
            #generate output path
            rel_path = os.path.relpath(os.path.dirname(f), input_dir)
            #out_path = os.path.join(output_dir, rel_path, os.path.basename(f))


            
            # lift over
#            if os.path.basename(f) == segment_input_file:
            if ((segment_input_file !=None) or (index_file != None)) and  (seg_pattern.match(os.path.basename(f))):
                if segment_output_file == None:
                    segment_output_file = seg_pattern.match(os.path.basename(f)).group(0)
                segment_out_path = os.path.join(output_dir, rel_path, segment_output_file)
                code = convertSegments(f, segment_out_path, chain_file,remapped_list, 
                                       remap_flag, new_segment_header)
                if code == 0:
                    seg_succ_counter += 1
                else:
                    seg_fail_counter += 1
#            elif os.path.basename(f) == probe_input_file:
            elif ((probe_input_file !=None) or (index_file !=None )) and (pro_pattern.match(os.path.basename(f))):
                if probe_output_file == None:
                    probe_output_file = pro_pattern.match(os.path.basename(f)).group(0)
                probe_out_path = os.path.join(output_dir, rel_path, probe_output_file)
                code = convertProbes(f, probe_out_path, chain_file, remapped_list, 
                                     remap_flag, new_probe_header)
                if code == 0:
                    pro_succ_counter += 1
                else:
                    pro_fail_counter += 1
            else:
                print('Unknown file type: ' + f)
                logger.error('Unknown file type: ' + f)
    
    if (seg_succ_counter + seg_fail_counter) >0:
        print('Segment files: {} processed, {} failed.'.format(seg_succ_counter, seg_fail_counter ))
    if (pro_succ_counter + pro_fail_counter) >0:
        print('Probe files: {} processed, {} failed'.format(pro_succ_counter, pro_fail_counter))


    # display global counts
    print('Total segments: {}'.format(total_seg))
    print('Lifted segments: {}'.format(lifted_seg))
    print('Remapped segments: {}'.format(remapped_seg))
    print('Rejected segments: {}'.format(rejected_seg))
    print('Unmapped segments: {}'.format(unmapped_seg))
    
    print('Total probes: {}'.format(total_pro))
    print('Lifted probes: {}'.format(lifted_pro))
    print('Remapped probes: {}'.format(remapped_pro))
    print('Rejected probes: {}'.format(rejected_pro))
    print('Unmapped probes: {}'.format(unmapped_pro))   


    # Save the remapped_list for reuse
    with open('./logs/remapped{}.log'.format(log_suffix), 'w') as fo:
        print('{}\t{}\t{}\t{}'.format('name', 'new_chr', 'new_pos', 'result'), file=fo)
        for k,v in remapped_list.items():
            print(k, end='', file=fo)
            for i in v:
                print('\t{}'.format(i), end='', file=fo)
            print('', file=fo)
    # Remove temp files.
    # subprocess.run('rm *.bed *.unmapped ./tmp/*.*  &>/dev/null', shell=True)
    subprocess.run('rm tmp/*', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('Done! Finished in {}'.format(datetime.now() - startTime))

##########################################################################
#
#                   Main
#
##########################################################################
if __name__ == '__main__':
    print()
    cli()
