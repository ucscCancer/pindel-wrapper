#!/usr/bin/env python

import logging
import argparse, os, shutil, subprocess, sys, tempfile, time, shlex, re
import datetime
from multiprocessing import Pool
import vcf

def execute(cmd, output=None):
    import subprocess, sys, shlex
    # function to execute a cmd and report if an error occur
    print(cmd)
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        stdout,stderr = process.communicate()
    except Exception, e: # une erreur de ma commande : stderr
        sys.stderr.write("problem doing : %s\n%s\n" %(cmd, e))
        return
    if output:
        output = open(output, 'w')
        output.write(stdout)
        output.close()
    if stderr != '': # une erreur interne au programme : stdout (sinon, souvent des warning arrete les programmes)
        sys.stdout.write("warning or error while doing : %s\n-----\n%s-----\n\n" %(cmd, stderr))


def indexBam(workdir, inputFastaFile, inputBamFile, bam_number, inputBamFileIndex=None):
    inputFastaLink = os.path.join(os.path.abspath(workdir), "reference.fa" )
    if not os.path.exists(inputFastaLink):
        os.symlink(inputFastaFile, inputFastaLink)
        cmd = "samtools faidx %s" %(inputFastaLink)
        execute(cmd)
    inputBamLink = os.path.join(os.path.abspath(workdir), "sample_%d.bam" % (bam_number) )
    os.symlink(inputBamFile, inputBamLink)
    if inputBamFileIndex is None:
        cmd = "samtools index %s" %(inputBamLink)
        execute(cmd)
    else:
        os.symlink(inputBamFileIndex, inputBamLink + ".bai")
    return inputFastaLink, inputBamLink


def config(inputBamFiles, meanInsertSizes, tags, tempDir):
    print("Creating Config File.")
    configFile = tempDir+"/pindel_configFile"
    fil = open(configFile, 'w')
    for inputBamFile, meanInsertSize, tag in zip(inputBamFiles, meanInsertSizes, tags):
        fil.write("%s\t%s\t%s\n" %(inputBamFile, meanInsertSize, tag))
    fil.close()
    return configFile


def pindel(reference, configFile, args, tempDir, chrome=None):
    if chrome is None:
        pindel_file_base = tempDir + "/pindel"
    else:
        pindel_file_base = tempDir + "/pindel_" + chrome

    cmd = "pindel -f %s -i %s -o %s " %(reference, configFile, pindel_file_base )

    if args.input_SV_Calls_for_assembly:
        cmd += ' --input_SV_Calls_for_assembly %s ' %(args.input_SV_Calls_for_assembly)

    if args.breakdancer:
        cmd += ' --breakdancer %s ' %(args.breakdancer)

    if args.exclude is not None:
        cmd += ' --exclude %s' % (args.exclude)

    if args.include is not None:
        cmd += ' --include %s' % (args.include)

    opt_list = [
        ["number_of_threads", "%d"],
        ["max_range_index", "%d"],
        ["window_size", "%d"],
        ["sequencing_error_rate", "%f"],
        ["sensitivity", "%f"],
        ["maximum_allowed_mismatch_rate", "%f"],
        ["NM", "%d"],
        ["additional_mismatch", "%d"],
        ["min_perfect_match_around_BP", "%d"],
        ["min_inversion_size", "%d"],
        ["min_num_matched_bases", "%d"],
        ["balance_cutoff", "%d"],
        ["anchor_quality", "%d"],
        ["minimum_support_for_event", "%d"]
    ]
    
    for o, f in opt_list:
        if getattr(args, o) is not None:
            cmd += (" --%s %s" % (o, f)) % (getattr(args,o))

    if chrome is not None:
        cmd += " -c '%s' " % (chrome)

    flag_list = [
        "report_long_insertions",
        "report_duplications",
        "report_inversions",
        "report_breakpoints",
        "report_close_mapped_reads",
        "report_only_close_mapped_reads",
        "report_interchromosomal_events",
        "IndelCorrection",
        "NormalSamples",
        "DD_REPORT_DUPLICATION_READS"
    ]

    for f in flag_list:
        if getattr(args, f):
            cmd += (" --%s" % (f))

    if args.detect_DD:
        cmd += ' -q '
        cmd += ' --MAX_DD_BREAKPOINT_DISTANCE '+str(args.MAX_DD_BREAKPOINT_DISTANCE)
        cmd += ' --MAX_DISTANCE_CLUSTER_READS '+str(args.MAX_DISTANCE_CLUSTER_READS)
        cmd += ' --MIN_DD_CLUSTER_SIZE '+str(args.MIN_DD_CLUSTER_SIZE)
        cmd += ' --MIN_DD_BREAKPOINT_SUPPORT '+str(args.MIN_DD_BREAKPOINT_SUPPORT)
        cmd += ' --MIN_DD_MAP_DISTANCE '+str(args.MIN_DD_MAP_DISTANCE)

    return (cmd, pindel_file_base )


def move(avant, apres):
    if os.path.exists(avant):
        execute("mv %s %s" %(avant, apres))


def pindel2vcf(inputFastaFile, refName, pindel_file, vcf_file):
    date = str(time.strftime('%d/%m/%y',time.localtime()))
    cmd = "pindel2vcf -p %s -r %s -R %s -d %s -v %s" % (pindel_file, inputFastaFile, refName, date, vcf_file)
    return cmd



def which(cmd):
    cmd = ["which",cmd]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    res = p.stdout.readline().rstrip()
    if len(res) == 0: return None
    return res


def get_bam_seq(inputBamFile, min_size=40000000): ### Changed min_size to 40mil. JHL
    samtools = which("samtools")
    cmd = [samtools, "idxstats", inputBamFile]
    process = subprocess.Popen(args=cmd, stdout=subprocess.PIPE)
    stdout, stderr = process.communicate()
    seqs = []
    for line in stdout.split("\n"):
        tmp = line.split("\t")
        if len(tmp) == 4 and int(tmp[1]) >= min_size:
            seqs.append(tmp[0])
    return seqs


def getMeanInsertSize(bamFile):
    logging.info("Getting insert size of %s" % (bamFile))
    cmd = "samtools view -f66 %s | head -n 1000000" % (bamFile)
    process = subprocess.Popen(args=cmd, shell=True, stdout=subprocess.PIPE)
    b_sum = 0L
    b_count = 0L
    while True:
        line = process.stdout.readline()
        if not line:
            break
        tmp = line.split("\t")
        if abs(long(tmp[8])) < 10000:
            b_sum += abs(long(tmp[8]))
            b_count +=1
    process.wait()
    mean = b_sum / b_count
    print "Using insert size: %d" % (mean)
    return mean



def __main__():
    logging.basicConfig(level=logging.INFO)
    time.sleep(1) #small hack, sometimes it seems like docker file systems aren't avalible instantly
    parser = argparse.ArgumentParser(description='')
    parser.add_argument('-r', dest='inputFastaFile', required=True, help='the reference file')
    parser.add_argument('-R', dest='inputFastaName', default="genome", help='the reference name')

    parser.add_argument('-b', dest='inputBamFiles', default=[], action="append", help='the bam file')
    parser.add_argument('-bi', dest='inputBamFileIndexes', default=[], action="append", help='the bam file')
    parser.add_argument('-s', dest='insert_sizes', type=int, default=[], action="append", required=False, help='the insert size')
    parser.add_argument('-t', dest='sampleTags', default=[], action="append", help='the sample tag')
    parser.add_argument('-o1', dest='outputRaw', help='the output raw', default=None)
    parser.add_argument('-o2', dest='outputVcfFile', help='the output vcf', default=None)
    parser.add_argument('-o3', dest='outputSomaticVcfFile', help='the output somatic filtered vcf', default=None)
    
    parser.add_argument('--number_of_threads', dest='number_of_threads', type=int, default=2)
    parser.add_argument('--number_of_procs', dest='procs', type=int, default=1)
    parser.add_argument('--breakdancer', dest='breakdancer')

    parser.add_argument('-x', '--max_range_index', dest='max_range_index', type=int, default=None)
    parser.add_argument('--window_size', dest='window_size', type=int, default=None)
    parser.add_argument('--sequencing_error_rate', dest='sequencing_error_rate', type=float, default=None)
    parser.add_argument('--sensitivity', dest='sensitivity', default=None, type=float)
    parser.add_argument('--report_long_insertions', dest='report_long_insertions', action='store_true', default=False)
    parser.add_argument('--report_duplications', dest='report_duplications', action='store_true', default=False)
    parser.add_argument('--report_inversions', dest='report_inversions', action='store_true', default=False)
    parser.add_argument('--report_breakpoints', dest='report_breakpoints', action='store_true', default=False)
    parser.add_argument('-u', '--maximum_allowed_mismatch_rate', dest='maximum_allowed_mismatch_rate', type=float, default=None)
    parser.add_argument('--report_close_mapped_reads', dest='report_close_mapped_reads', action='store_true', default=False)
    parser.add_argument('--report_only_close_mapped_reads', dest='report_only_close_mapped_reads', action='store_true', default=False)
    parser.add_argument('--report_interchromosomal_events', dest='report_interchromosomal_events', action='store_true', default=False)
    parser.add_argument('--IndelCorrection', dest='IndelCorrection', action='store_true', default=False)
    parser.add_argument('--NormalSamples', dest='NormalSamples', action='store_true', default=False)
    parser.add_argument('-a', '--additional_mismatch', dest='additional_mismatch', type=int, default=None)
    parser.add_argument('-m', '--min_perfect_match_around_BP', dest='min_perfect_match_around_BP', type=int, default=None)
    parser.add_argument('-v', '--min_inversion_size', dest='min_inversion_size', type=int, default=None)
    parser.add_argument('-d', '--min_num_matched_bases', dest='min_num_matched_bases', type=int, default=None)
    parser.add_argument('-B', '--balance_cutoff', dest='balance_cutoff', type=int, default=None)
    parser.add_argument('-A', '--anchor_quality', dest='anchor_quality', type=int, default=None)
    parser.add_argument('-M', '--minimum_support_for_event', dest='minimum_support_for_event', type=int, default=None)
    parser.add_argument('-n', '--NM', dest='NM', type=int, default=None)
    parser.add_argument('--detect_DD', dest='detect_DD', action='store_true', default=False)
    parser.add_argument('--MAX_DD_BREAKPOINT_DISTANCE', dest='MAX_DD_BREAKPOINT_DISTANCE', type=int, default='350')
    parser.add_argument('--MAX_DISTANCE_CLUSTER_READS', dest='MAX_DISTANCE_CLUSTER_READS', type=int, default='100')
    parser.add_argument('--MIN_DD_CLUSTER_SIZE', dest='MIN_DD_CLUSTER_SIZE', type=int, default='3')
    parser.add_argument('--MIN_DD_BREAKPOINT_SUPPORT', dest='MIN_DD_BREAKPOINT_SUPPORT', type=int, default='3')
    parser.add_argument('--MIN_DD_MAP_DISTANCE', dest='MIN_DD_MAP_DISTANCE', type=int, default='8000')
    parser.add_argument('--DD_REPORT_DUPLICATION_READS', dest='DD_REPORT_DUPLICATION_READS', action='store_true', default=False)

    parser.add_argument('--somatic_vaf', type=float, default=0.08)
    parser.add_argument('--somatic_cov', type=int, default=20)
    parser.add_argument('--somatic_hom', type=int, default=6)

    parser.add_argument("-J", "--exclude", dest="exclude", default=None)
    parser.add_argument("-j", "--include", dest="include", default=None)

    parser.add_argument('-z', '--input_SV_Calls_for_assembly', dest='input_SV_Calls_for_assembly', action='store_true', default=False)

    parser.add_argument('--workdir', default="./")
    parser.add_argument('--no_clean', action="store_true", default=False)

    args = parser.parse_args()

    inputBamFiles = list( os.path.abspath(a) for a in args.inputBamFiles )
    if len(inputBamFiles) == 0:
        logging.error("Need input files")
        sys.exit(1)
    inputBamFileIndexes = list( os.path.abspath(a) for a in args.inputBamFileIndexes )

    if len(inputBamFileIndexes) == 0:
        inputBamFileIndexes = [None] * len(inputBamFiles)
    if len(inputBamFileIndexes) != len(inputBamFiles):
        logging.error("Index file count needs to undefined or match input file count")
        sys.exit(1)
    insertSizes = args.insert_sizes
    if len(insertSizes) == 0:
        insertSizes = [None] * len(inputBamFiles)
    if len(insertSizes) != len(inputBamFiles):
        logging.error("Insert Sizes needs to undefined or match input file count")
        sys.exit(1)

    sampleTags = args.sampleTags
    if len(sampleTags) != len(inputBamFiles):
        logging.error("Sample Tags need to match input file count")
        sys.exit(1)

    tempDir = tempfile.mkdtemp(dir=args.workdir, prefix="pindel_work_")
    print(tempDir)
    try:
        meanInsertSizes = []
        seq_hash = {}
        newInputFiles = []
        i = 0
        #make sure the BAMs are indexed and get the mean insert sizes
        for inputBamFile, inputBamIndex, insertSize, sampleTag in zip(inputBamFiles, inputBamFileIndexes, insertSizes, sampleTags ):
            inputFastaFile, inputBamFile = indexBam(args.workdir, args.inputFastaFile, inputBamFile, i, inputBamIndex)
            i += 1
            newInputFiles.append(inputBamFile)
            if insertSize==None:
                meanInsertSize = getMeanInsertSize(inputBamFile)
            else:
                meanInsertSize=insertSize
            meanInsertSizes.append( meanInsertSize )
            for seq in get_bam_seq(inputBamFile):
                seq_hash[seq] = True
        seqs = seq_hash.keys()
        configFile = config(newInputFiles, meanInsertSizes, sampleTags, tempDir)

        #run pindel
        pindel_files = []
        if args.procs == 1:
            cmd, pindelFileBase = pindel(inputFastaFile, configFile, args, tempDir)
            execute(cmd)
            for suffix in ["_D", "_SI", "_LI", "_INV", "_TD"]:
                if os.path.exists(pindelFileBase + suffix):
                    pindel_files.append( pindelFileBase + suffix )
        else:
            cmds = []
            runs = []
            for a in seqs:
                cmd, pindelFileBase = pindel(inputFastaFile, configFile, args, tempDir, a)
                cmds.append(cmd)
                runs.append(pindelFileBase)
            p = Pool(args.procs)
            values = p.map(execute, cmds, 1)
            for pindelFileBase in runs:
                for suffix in ["_D", "_SI", "_LI", "_INV", "_TD"]:
                    if os.path.exists(pindelFileBase + suffix):
                        pindel_files.append( pindelFileBase + suffix )

        #run pindel2vcf
        with open(os.path.join(args.workdir, "pindel_all"), "w") as handle:
            for p in pindel_files:
                with open(p) as ihandle:
                    for line in ihandle:
                        handle.write(line)

        if args.outputRaw is not None:
            shutil.copy(os.path.join(args.workdir, "pindel_all"), args.outputRaw)

        if args.outputVcfFile is not None:
            cmd = pindel2vcf(inputFastaFile, args.inputFastaName, os.path.join(args.workdir, "pindel_all"), args.outputVcfFile)
            execute(cmd)
        
        if args.outputSomaticVcfFile is not None:
            with open(os.path.join(args.workdir, "pindel_somatic"), "w") as handle:
                for p in pindel_files:
                    if p.endswith("_D"):
                        with open(p) as ihandle:
                            for line in ihandle:
                                if re.search("ChrID", line):
                                    handle.write(line)
                for p in pindel_files:
                    if p.endswith("_SI"):
                        with open(p) as ihandle:
                            for line in ihandle:
                                if re.search("ChrID", line):
                                    handle.write(line)
            
            with open(os.path.join(args.workdir, "somatic.indel.filter.config"), "w") as handle:
                handle.write("indel.filter.input = %s\n" % os.path.join(args.workdir, "pindel_somatic"))
                handle.write("indel.filter.vaf = %s\n" % (args.somatic_vaf))
                handle.write("indel.filter.cov = %s\n" % (args.somatic_cov))
                handle.write("indel.filter.hom = %s\n" % (args.somatic_hom))
                handle.write("indel.filter.pindel2vcf = %s\n" % (which("pindel2vcf")))
                handle.write("indel.filter.reference =  %s\n" % (inputFastaFile))
                handle.write("indel.filter.referencename = %s\n" % (args.inputFastaName))
                handle.write("indel.filter.referencedate = %s\n" % (datetime.datetime.now().strftime("%Y%m%d")) )
                handle.write("indel.filter.output = %s\n" % (args.outputSomaticVcfFile))
            
            execute("%s /home/exacloud/clinical/RichardsLab/bin/somatic_indelfilter.pl %s" % (which("perl"), os.path.join(args.workdir, "somatic.indel.filter.config")) )
            
                

    finally:
        if not args.no_clean and os.path.exists(tempDir):
            shutil.rmtree(tempDir)

if __name__=="__main__":
    __main__()
