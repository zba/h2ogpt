import ast
import concurrent.futures
import contextlib
import hashlib
import json
import os
import shutil
import signal
import sys
import threading
import traceback
from concurrent import futures
from concurrent.futures import ProcessPoolExecutor

import psutil
import pytest
from datasets import load_dataset
import pandas as pd


def parse_rst_file(filepath):
    with open(filepath, 'r') as f:
        input_data = f.read()
    settings_overrides = {'initial_header_level': 2}
    from docutils import core
    document = core.publish_doctree(
        source=input_data,
        source_path=filepath,
        settings_overrides=settings_overrides,
    )
    qa_pairs = []
    current_section = None
    current_question = ""
    current_answer = ""
    for node in document.traverse():
        if node.__class__.__name__ == 'section':
            current_section = ""
        elif current_section is not None:
            if node.__class__.__name__ == 'Text':
                if node.astext()[-1] == "?":
                    if current_question:
                        qa_pairs.append((current_question, current_answer))
                    current_question = node.astext()
                    current_answer = ""
                else:
                    current_answer += node.astext()
    if current_answer:
        qa_pairs.append((current_question, current_answer))
    return {k: v for k, v in qa_pairs}


def test_scrape_dai_docs():
    home = os.path.expanduser('~')
    file = os.path.join(home, 'h2oai/docs/faq.rst')
    qa_pairs = parse_rst_file(file)
    prompt_type = 'human_bot'
    from finetune import prompt_types
    assert prompt_type in prompt_types
    save_thing = [{"instruction": k, "output": v, 'prompt_type': prompt_type} for k, v in qa_pairs.items()]
    output_file = "dai_faq.json"
    with open(output_file, "wt") as f:
        f.write(json.dumps(save_thing, indent=2))


def test_scrape_dai_docs_all():
    """
    pytest scrape_dai_docs.py::test_scrape_dai_docs_all
    """
    import numpy as np
    import glob
    import nltk
    nltk.download('punkt')
    dd = {}
    np.random.seed(1234)
    home = os.path.expanduser('~')
    files = list(glob.glob(os.path.join(home, "h2oai/docs/**/*rst")))
    np.random.shuffle(files)
    val_count = int(0.05 * len(files))
    train_files = files[val_count:]
    valid_files = files[:val_count]
    things = [
        ("dai_docs.train.json", train_files),
        ("dai_docs.valid.json", valid_files)
    ]
    for LEN in [100, 200, 500]:
        for output_file, ff in things:
            if output_file not in dd:
                dd[output_file] = []
            for f in ff:
                with open(f) as input:
                    blob = input.read()
                    blob = blob.replace("~~", "")
                    blob = blob.replace("==", "")
                    blob = blob.replace("''", "")
                    blob = blob.replace("--", "")
                    blob = blob.replace("**", "")
                    dd[output_file].extend(get_sentences(blob, length=LEN))
    for output_file, _ in things:
        save_thing = [{"output": k.strip(), 'prompt_type': 'plain'} for k in dd[output_file]]
        with open(output_file, "wt") as f:
            f.write(json.dumps(save_thing, indent=2))


def get_sentences(blob, length):
    """
    break-up input text into sentences and then output list of sentences of about length in size
    :param blob:
    :param length:
    :return:
    """
    from nltk.tokenize import sent_tokenize
    sentences = sent_tokenize(blob)
    my_sentences = []
    my_string = ""
    for sentence in sentences:
        if len(my_string) < length:
            my_string += " " + sentence
        else:
            my_sentences.append(my_string)
            my_string = ""
    return my_sentences


def test_scrape_dai_docs_all_pandoc():
    """
    pytest -s -v scrape_dai_docs.py::test_scrape_dai_docs_all_pandoc
    :return:
    """
    # account for sequence length (context window) including prompt and input and output
    MAX_LEN = 2048//2 - 30
    MIN_LENGTH = 30  # to avoid bare headers

    home = os.path.expanduser('~')
    import glob
    files = list(glob.glob(os.path.join(home, "h2oai/docs/**/*"), recursive=True))

    # pandoc can't find include files
    dst = "working_dir_docs"
    remove(dst)
    os.makedirs(dst)

    # copy full tree, for absolute paths in rst
    for fil in files:
        if os.path.isfile(fil):
            shutil.copy(fil, dst)

    files = list(glob.glob(os.path.join(dst, '*rst'), recursive=True))
    # hack for relative path
    scorers_dir = os.path.join(dst, 'scorers')
    makedirs(scorers_dir)
    for fil in glob.glob(os.path.join(dst, '*.frag')):
        shutil.copy(fil, scorers_dir)

    # os.system('pandoc -f rst -t plain ./expert_settings/nlp_settings.rst')
    import pypandoc
    outputs = []
    basedir = os.path.abspath(os.getcwd())

    for fil in files:
        os.chdir(basedir)
        os.chdir(os.path.dirname(fil))
        fil = os.path.basename(fil)
        print("Processing %s" % fil, flush=True)
        # out_format can be one of: asciidoc, asciidoctor, beamer, biblatex, bibtex, commonmark, commonmark_x,
        # context, csljson, docbook, docbook4, docbook5, docx, dokuwiki,
        # dzslides, epub, epub2, epub3, fb2, gfm, haddock, html, html4, html5, icml,
        # ipynb, jats, jats_archiving, jats_articleauthoring, jats_publishing, jira,
        # json, latex, man,
        # markdown, markdown_github, markdown_mmd, markdown_phpextra, markdown_strict,
        # mediawiki, ms, muse, native, odt, opendocument, opml, org, pdf, plain, pptx,
        # revealjs, rst, rtf, s5, slideous, slidy, tei, texinfo, textile, xwiki, zimwiki
        out_format = 'plain'
        # avoid extra new lines injected into text
        extra_args = ['--wrap=preserve', '--resource path="%s" % dst']

        plain_list = []
        try:
            # valid for expert settings
            input_rst = pypandoc.convert_file(fil, 'rst')
            input_list = input_rst.split('\n``')
            for input_subrst in input_list:
                input_plain = pypandoc.convert_text(input_subrst, format='rst', to='plain')
                plain_list.append(input_plain)
        except Exception as e:
            print("file exception: %s %s" % (fil, str(e)), flush=True)

        if not plain_list:
            # if failed to process as pieces of rst, then
            output = pypandoc.convert_file(fil, out_format, extra_args=extra_args, format='rst')
            outputs = get_sentences(output, length=MAX_LEN)
            for oi, output in enumerate(outputs):
                output = output.replace('\n\n', '\n')
                plain_list.append(output)
        outputs.extend(plain_list)

    # report:
    # [print(len(x)) for x in outputs]

    # deal with blocks longer than context size (sequence length) of 2048
    new_outputs = []
    num_truncated = 0
    num_orig = len(outputs)
    for output in outputs:
        if len(output) < MAX_LEN:
            new_outputs.append(output)
            continue
        outputs1 = get_sentences(output, length=MAX_LEN)
        for oi, output1 in enumerate(outputs1):
            output1 = output1.replace('\n\n', '\n')
            new_outputs.append(output1)
        num_truncated += 1
    print('num_orig: %s num_truncated: %s' % (num_orig, num_truncated), flush=True)

    os.chdir(basedir)
    remove(dst)
    save_thing = [{"output": k.strip(), 'prompt_type': 'plain'} for k in new_outputs if len(k) > MIN_LENGTH]
    output_file = "dai_docs.train_cleaned.json"
    with open(output_file, "wt") as f:
        f.write(json.dumps(save_thing, indent=2))


def remove(path: str):
    try:
        if path is not None and os.path.exists(path):
            if os.path.isdir(path):
                shutil_rmtree(path, ignore_errors=True)
            else:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(path)
    except:
        pass


def shutil_rmtree(*args, **kwargs):
    return shutil.rmtree(*args, **kwargs)


def test_config_to_json():
    """
    E.g. (base) jon@gpu:~/h2oai$ pytest -s -v /data/jon/h2o-llm/scrape_dai_docs.py::test_config_to_json ; cp config.json /data/jon/h2o-llm/
    :return:
    """
    try:
        # Arrange
        import json
        from h2oaicore.systemutils import config
        toml_list = []
        for k, v in config.get_meta_dict().items():
            title = (v.title + ": ") if v.title else ''
            comment = v.comment or ''
            if not (title or comment):
                continue
            toml_list.extend(
                [
                    {
                        'prompt_type': 'human_bot',
                        'instruction': f'Explain the following expert setting for Driverless AI',
                        'input': f"{k}",
                        'output': f"{k.replace('_', ' ')} config.toml: {comment or title}".replace("\n", ""),
                    },
                    {
                        'prompt_type': 'human_bot',
                        'instruction': f'Explain the following expert setting for Driverless AI',
                        'input': f"{k}",
                        'output': f"{k.replace('_', ' ')} config.toml: {title}{comment}".replace("\n", ""),
                    },
                    {
                        'prompt_type': 'human_bot',
                        'instruction': f'Explain the following expert setting for Driverless AI',
                        'input': f"{k.replace('_', ' ')}",
                        'output': f"{k.replace('_', ' ')} config.toml: {title}{comment}".replace("\n", ""),
                    },
                    {
                        'prompt_type': 'human_bot',
                        'instruction': f'Explain the following expert setting for Driverless AI',
                        'input': f"{title}",
                        'output': f"{k.replace('_', ' ')} config.toml: {title}{comment}".replace("\n", ""),
                    },
                    {
                        'prompt_type': 'human_bot',
                        'instruction': f'Provide a short explanation of the expert setting {k}',
                        'output': f"{k.replace('_', ' ')} config.toml: {comment or title}".replace("\n", ""),
                    },
                    {
                        'prompt_type': 'human_bot',
                        'instruction': f'Provide a detailed explanation of the expert setting {k}',
                        'output': f"{k.replace('_', ' ')} config.toml: {title}{comment}".replace("\n", ""),
                    },
                ]
            )
        with open("config.json", "wt") as f:
            f.write(json.dumps(toml_list, indent=2))
    except Exception as e:
        print("Exception: %s" % str(e), flush=True)


def copy_tree(src, dst, follow_symlink=False):
    makedirs(dst, exist_ok=True)
    for (path, dirs, files) in os.walk(src, followlinks=follow_symlink):
        new_path = path.replace(src, dst)
        makedirs(new_path, exist_ok=True)
        for file in files:
            filename = os.path.join(path, file)
            new_filename = os.path.join(new_path, file)
            # print("%s -> %s" % (filename, new_filename))
            try:
                atomic_copy(filename, new_filename)
            except FileNotFoundError:
                pass


def atomic_move(src, dst):
    try:
        shutil.move(src, dst)
    except (shutil.Error, FileExistsError):
        pass
    remove(src)


def atomic_copy(src=None, dst=None, with_permissions=True):
    if os.path.isfile(dst):
        return
    import uuid
    my_uuid = uuid.uuid4()
    dst_tmp = dst + str(my_uuid)
    makedirs(os.path.dirname(dst), exist_ok=True)
    if with_permissions:
        shutil.copy(src, dst_tmp)
    else:
        shutil.copyfile(src, dst_tmp)
    atomic_move(dst_tmp, dst)
    remove(dst_tmp)


def makedirs(path, exist_ok=True):
    """
    Avoid some inefficiency in os.makedirs()
    :param path:
    :param exist_ok:
    :return:
    """
    if os.path.isdir(path) and os.path.exists(path):
        assert exist_ok, "Path already exists"
        return path
    os.makedirs(path, exist_ok=exist_ok)


## Download from https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/ShareGPT_unfiltered_cleaned_split.json
## Turn into simple instruct prompt type. No context/previous conversations.
def test_prep_vicuna_instruct():
    filename = 'ShareGPT_unfiltered_cleaned_split.json'
    if not os.path.exists(filename):
        os.system('wget https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/%s' % filename)
    data = load_dataset("json", data_files={"train": filename})["train"]
    training_rows = []
    for i in range(data.num_rows):
        conversations = data[i]['conversations']
        assert isinstance(conversations, list), conversations
        convo = ""
        for j, conv in enumerate(conversations):
            # Get ready for generate.py prompt_type=human_bot
            # But train with prompt_type=plain
            if conv['from'] == 'human':
                FROM = '<human>: '
            elif conv['from'] == 'gpt':
                FROM = '<bot>: '
            convo += f"{FROM}" + conv['value'] + "\n"
        if convo:
            training_rows.append(dict(input=convo))
    with open(filename + ".generate_human_bot.train_plain.json", "wt") as f:
        f.write(json.dumps(training_rows, indent=2))

POSTFIX = ".generate_human_bot.train_plain.json"

# https://bair.berkeley.edu/blog/2023/04/03/koala/
OIG_DATASETS = [
    "unified_chip2.jsonl",
    "unified_grade_school_math_instructions.jsonl",
    "unified_poetry_2_song.jsonl",
    "unified_plot_screenplay_books_dialog.jsonl",
]

# hub issue: https://huggingface.co/datasets/laion/OIG/discussions/4
ALL_OIG_DATASETS = ['unified_abstract_infill.jsonl',
                    'unified_basic.jsonl',
                    'unified_canadian_parliament.jsonl',
                    'unified_chip2.jsonl',
                    'unified_conv_finqa.jsonl',
                    'unified_cuad.jsonl',
                    'unified_essays.jsonl',
                    'unified_flan.jsonl.gz',
                    'unified_grade_school_math_instructions.jsonl',
                    'unified_hc3_human.jsonl',
                    'unified_image_prompts_instructions.jsonl',
                    'unified_joke_explanations.jsonl',
                    'unified_mathqa_flanv2_kojma_cot.jsonl',
                    'unified_merged_code_xp3.jsonl',
                    'unified_multi_news.jsonl',
                    'unified_multi_sum.jsonl',
                    'unified_ni.jsonl.gz',
                    'unified_nq.jsonl',
                    'unified_openai_summarize_tldr.jsonl',
                    'unified_oscar_en_sample_dialog.jsonl',
                    'unified_p3.jsonl.gz',
                    'unified_plot_screenplay_books_dialog.jsonl',
                    'unified_poetry_2_song.jsonl',
                    'unified_poetry_instructions.jsonl',
                    'unified_rallio_safety_and_prosocial.jsonl',
                    'unified_rallio_soda_upgraded_2048.jsonl',
                    'unified_soda_dialog.jsonl',
                    'unified_sqlv1.jsonl',
                    'unified_sqlv2.jsonl',
                    'unified_squad_v2.jsonl',
                    'unified_squad_v2_more_neg.jsonl',
                    'unified_ul2_plus_oscar_en_sample_dialog.jsonl',
                    'unified_unifiedskg_instructions.jsonl',
                    'unified_unnatural_instructions.jsonl',
                    'unified_xp3_sample.jsonl']


@pytest.mark.parametrize("filename", OIG_DATASETS)
def test_get_OIG_data(filename):
    if not os.path.exists(filename):
        os.system('wget https://huggingface.co/datasets/laion/OIG/resolve/main/%s' % filename)
    import json
    rows = []
    with open(filename, "r") as f:
        for line in f.readlines():
            row = json.loads(line)
            rows.append(dict(input=row["text"]))
    with open(filename + POSTFIX, "w") as f:
        f.write(json.dumps(rows, indent=2))


@pytest.mark.parametrize("filename", ALL_OIG_DATASETS)
def test_get_OIG_data_as_parquet(filename):
    if not os.path.exists(filename):
        os.system('wget https://huggingface.co/datasets/laion/OIG/resolve/main/%s' % filename)
    df = pd.read_json(path_or_buf=filename, lines=True)
    df.to_parquet(filename + '.parquet', index=False)


def test_merge_shuffle_OIG_data():
    import numpy as np
    np.random.seed(1234)
    rows = []
    for filename in OIG_DATASETS:
        with open(filename + POSTFIX, "r") as f:
            rows.extend(json.loads(f.read()))
    np.random.shuffle(rows)
    with open("merged_shuffled_OIG_%s.json" % hashlib.sha256(str(OIG_DATASETS).encode()).hexdigest()[:10], "w") as f:
        f.write(json.dumps(rows, indent=2))


def test_join_jsons():
    files = ['alpaca_data_cleaned.json'] * 0 + \
             ['config.json'] * 1 + \
             ['dai_docs.train_cleaned.json'] * 2 + \
             ['dai_faq.json'] * 3
    print(files)
    lst = []
    [lst.extend(json.load(open(fil, 'rt'))) for fil in files]
    print(len(lst))
    json.dump(lst, open("merged.json", "wt"), indent=2)


@pytest.mark.parametrize("filename", ['Anthropic/hh-rlhf'])
def test_make_rlhf_good_data(filename):
    rows = load_dataset(filename)["train"]["chosen"]
    new_rows = []
    for row in rows:
        if row[:2] == "\n\n":
            row = row[2:]
        row = row.replace("Human: ", "<human>: ")
        row = row.replace("Assistant: ", "<bot>: ")
        new_rows.append(dict(input=row))
    with open(filename.replace("/", "_") + POSTFIX, "w") as f:
        f.write(json.dumps(new_rows, indent=2))



def test_show_prompts():
    files = ['alpaca_data_cleaned.json'] * 0 + \
             ['config.json'] * 1 + \
             ['dai_docs.train_cleaned.json'] * 1 + \
             ['dai_faq.json'] * 1
    file_points = [json.load(open(fil, 'rt')) for fil in files]
    from finetune import generate_prompt
    for data_points in file_points:
        for data_point in data_points:
            print(generate_prompt(data_point, 'plain')[0])


def flatten_list(lis):
    """Given a list, possibly nested to any level, return it flattened."""
    new_lis = []
    for item in lis:
        if type(item) == type([]):
            new_lis.extend(flatten_list(item))
        else:
            new_lis.append(item)
    return new_lis


def test_get_open_datasets():
    from huggingface_hub import list_datasets
    datasets = list_datasets()
    # check all:
    all_license_tags = set(flatten_list([[y for y in x.tags if 'license' in y] for x in datasets]))
    print(len(all_license_tags))
    open_tags = ['license:Apache License 2.0',
                 'license:mit',
                 'license:apache',
                 'license:apache2',
                 'license:apache-2.0',
                 'license:bsd',
                 'license:bsd-2-clause',
                 'license:bsd-3-clause',
                 'license:bsd-3-clause-clear',
                 'license:lgpl-2.1',
                 'license:lgpl-3.0',
                 'license:lgpl-lr',
                 'license:lgpl',
                 'license:openrail++',
                 'license:openrail',
                 'license:bigscience-bloom-rail-1.0',
                 #'license:agpl-3.0',
                 'license:other',
                 'license:unknown',
                 # 'license:mpl-2.0',
                 ]
    open_datasets = [x for x in datasets if any([y in x.tags for y in open_tags]) or 'license:' not in str(x.tags)]
    print('open_datasets', len(open_datasets))
    all_task_tags = set(flatten_list([[y for y in x.tags if 'task' in y] for x in open_datasets]))
    print('all_task_tags', len(all_task_tags))
    excluded_tags = ['image', 'hate', 'tabular', 'table-', 'classification', 'retrieval',
                     'translation', 'identification', 'object', 'mask', 'to-text',
                     'face-detection', 'audio', 'voice', 'reinforcement', 'depth-est',
                     'forecasting', 'parsing', 'visual', 'speech', 'multiple-choice',
                     'slot-filling', 'irds/argsme', '-scoring', 'other', 'graph-ml',
                     'feature-extraction', 'keyword-spotting',
                     'coreference-resolution', 'segmentation',
                     'word-sense-disambiguation',
                     'lemmatization']
    task_tags = [x.replace('task_categories:', '').replace('task_ids:', '')
                 for x in all_task_tags if not any([y in x for y in
                                                    excluded_tags])]
    print('task_tags', len(task_tags))
    # str(x.tags) to catch any pattern match to anything in list
    open_tasked_datasets = [x for x in open_datasets if
                            any([y in str([x for x in x.tags if 'task' in x]) for y in task_tags]) and
                            not any([y in str([x for x in x.tags if 'task' in x]) for y in excluded_tags]) or
                            'task_categories' not in str(x.tags) and 'task_ids' not in str(x.tags)]
    open_tasked_datasets = [x for x in open_tasked_datasets if not x.disabled]
    open_tasked_datasets = [x for x in open_tasked_datasets if not x.gated]
    open_tasked_datasets = [x for x in open_tasked_datasets if not x.private]
    print('open_tasked_datasets', len(open_tasked_datasets))
    sizes = list(set(flatten_list([[(y, x.id) for y in x.tags if 'size' in y] for x in open_tasked_datasets])))
    languages = list(set(flatten_list([[(y, x.id) for y in x.tags if 'language:' in y] for x in open_tasked_datasets])))
    open_english_tasked_datasets = [x for x in open_tasked_datasets if
                                    'language:' not in str(x.tags) or
                                    'language:en' in str(x.tags)]
    small_open_english_tasked_datasets = [x for x in open_english_tasked_datasets if
                                    'n<1K' in str(x.tags) or
                                    '1K<n<10K' in str(x.tags) or
                                    '1K0<n<100K' in str(x.tags) or
                                    '100K<n<1M' in str(x.tags) or
                                    'size_category' not in str(x.tags)
                                    ]
    # 'aeslc' : email_body, subject -> summarization?
    # load_dataset(open_tasked_datasets[0].id).data['train'].to_pandas()
    ids = [x.id for x in small_open_english_tasked_datasets]

    # sanity checks
    # https://bair.berkeley.edu/blog/2023/04/03/koala/
    assert 'alespalla/chatbot_instruction_prompts' in ids
    assert 'laion/OIG' in ids
    assert 'openai/webgpt_comparisons' in ids
    assert 'openai/summarize_from_feedback' in ids
    assert 'Anthropic/hh-rlhf' in ids

    # useful but not allowed for commercial purposes:
    # https://huggingface.co/datasets/squad

    print('open_english_tasked_datasets: ', ids, flush=True)

    exclude_ids = ['allenai/nllb',  # translation only
                   'hf-internal-testing/fixtures_image_utils',  # testing
                   'allenai/c4',  # search-url
                   'agemagician/uniref50',  # unknown
                   'huggingface-course/documentation-images',  # images
                   'smilegate-ai/kor_unsmile',  # korean
                   'MohamedRashad/ChatGPT-prompts',  # ChatGPT/LearnGPT/https://www.emergentmind.com/
                   'humarin/chatgpt-paraphrases',  # Paraphrase using ChatGPT
                   'Jeska/vaccinchat',  # not useful
                   'alespalla/chatbot_instruction_prompts',  # mixes alpaca
                   'allenai/prosocial-dialog',  # already exlucded, but wrongly in other datasets that say more permissive license
                   'AlekseyKorshuk/persona-chat',  # low quality
                   'bavard/personachat_truecased',  # low quality
                   'adamlin/daily_dialog',  # medium quality conversations
                   'adamlin/FewShotWoz',  # low quality
                   'benjaminbeilharz/better_daily_dialog',  # low quality
                   'benjaminbeilharz/daily_dialog_w_turn_templates',  # low
                   'benjaminbeilharz/empathetic_dialogues_for_lm',  # low
                   'GEM-submissions/GEM__bart_base_schema_guided_dialog__1645547915',  # NA
                   'ia-bentebib/conv_ai_2_fr',  # low fr
                   'ia-bentebib/daily_dialog_fr',  # low fr
                   'ia-bentebib/dialog_re_fr',  # low fr
                   'ia-bentebib/empathetic_dialogues_fr',  # low fr
                   'roskoN/dailydialog',  # low
                   'VadorMazer/skyrimdialogstest',  # low
                   'bigbio/med_qa',  # med specific Q/A
                   'biu-nlp/qa_srl2018',  # low quality Q/A
                   'biu-nlp/qa_discourse',  # low quality Q/A
                   'iarfmoose/qa_evaluator',  # low quality Q/A
                   'jeopardy',  # low quality Q/A -- no reasoning
                   'narrativeqa',  # low quality Q/A
                   'nomic-ai/gpt4all_prompt_generations',  # bad license
                   'nomic-ai/gpt4all_prompt_generations_with_p3',  # bad license
                   'HuggingFaceH4/alpaca',  # bad license
                   'Hello-SimpleAI/HC3',  # bad license
                   ]
    small_open_english_tasked_datasets = [x for x in small_open_english_tasked_datasets if x.id not in exclude_ids]
    # some ids clearly speech related
    small_open_english_tasked_datasets = [x for x in small_open_english_tasked_datasets if 'speech' not in x.id]
    # HF testing
    small_open_english_tasked_datasets = [x for x in small_open_english_tasked_datasets if 'hf-internal-testing' not in x.id]
    small_open_english_tasked_datasets = [x for x in small_open_english_tasked_datasets if
                                          'chinese' not in x.id]

    sorted_small_open_english_tasked_datasets = sorted([(x.downloads, x) for x in small_open_english_tasked_datasets],
                                                       key=lambda x: x[0], reverse=True)

    # NOTES:
    # Run like pytest -s -v scrape_dai_docs.py::test_get_open_datasets &> getdata9.log
    # See what needs config passed and add:
    # grep 'load_dataset(' getdata9.log|grep -v data_id|less -S
    # grep "pip install" getdata9.log
    # NOTE: Some datasets have default config, but others are there.  Don't know how to access them.


    """
    https://huggingface.co/datasets/wikihow/blob/main/wikihow.py
    https://github.com/mahnazkoupaee/WikiHow-Dataset
    https://ucsb.box.com/s/ap23l8gafpezf4tq3wapr6u8241zz358
    https://ucsb.app.box.com/s/ap23l8gafpezf4tq3wapr6u8241zz358
    """

    """
    # some ambiguous or non-commercial datasets
    https://github.com/PhoebusSi/alpaca-CoT
    """

    # grep columns getdata13.log|grep -v "\['image'\]"|sort|uniq|grep -v tokens|grep -v "'image'"|grep -v embedding|grep dialog
    useful = ['Dahoas/instruct-human-assistant-prompt',
              'Dahoas/first-instruct-human-assistant-prompt',
              'knkarthick/dialogsum',  # summary of conversation
              'McGill-NLP/FaithDial',  # medium quality
              'Zaid/quac_expanded',  # medium quality context + Q + A
              '0-hero/OIG-small-chip2',  # medium
              'alistvt/coqa-flat',  # QA medium
              'AnonymousSub/MedQuAD_47441_Question_Answer_Pairs',  # QA medium
              'Anthropic/hh-rlhf',  # high quality  # simmilar to Dahoas/full-hh-rlhf
              'arjunth2001/online_privacy_qna',  # good quality Q/A
              'Dahoas/instruct_helpful_preferences',  # medium quality instruct
              'Dahoas/rl-prompt-dataset',  # medium chat
              'Dahoas/rm-static',  # medium chat
              'Dahoas/static-hh',  # medium chat  # HuggingFaceH4/self_instruct
              'Dahoas/synthetic-instruct-gptj-pairwise',  # medium chat
              'eli5',  # Q/A if prompt ELI5
              'gsm8k',  # Q/A (various)
              'guanaco/guanaco',  # prompt/response
              'kastan/rlhf-qa-comparisons',  # good Q/A
              'kastan/rlhf-qa-conditional-generation-v2',  # prompt answer
              'OllieStanley/humaneval-mbpp-codegen-qa',  # code QA, but started from words, so better than other code QA
              'OllieStanley/humaneval-mbpp-testgen-qa',  # code QA
              'Graverman/Instruct-to-Code',  # code QA
              'openai/summarize_from_feedback',  # summarize
              'relbert/analogy_questions',  # analogy Q/A
              'yitingxie/rlhf-reward-datasets',  # prompt, chosen, rejected.
              'yizhongw/self_instruct',  # instruct (super natural & instruct)
              'HuggingFaceH4/asss',  # Q/A, big A
              'kastan/rlhf-qa-conditional-generation-v2',  # QA
              ]

    code_useful = ['0n1xus/codexglue',
    'openai_humaneval',
                   ]
    maybe_useful = ['AlekseyKorshuk/comedy-scripts',
                     'openbookqa',  # hard to parse, low reasoning
                    'qed',  # reasonable QA, but low reasoning
                    'selqa',  # candidate answers
                    'HuggingFaceH4/instruction-pilot-outputs-filtered',
                    ]
    summary_useful = ['austin/rheum_abstracts',
                      'CarperAI/openai_summarize_comparisons',  # summarize chosen/rejected
                      'CarperAI/openai_summarize_tldr',  # summarize Q/A
                      'ccdv/cnn_dailymail',  # summarize news
                      'ccdv/govreport-summarization',  # summarize high quality
                      'ccdv/pubmed-summarization',  # summarize high quality
                      'duorc',  # plot -> Q/A
                      'farleyknight/big_patent_5_percent',  # desc -> abstract
                      'multi_news',  # summary
                      'opinosis',
                      'SophieTr/reddit_clean',
                      ]
    math_useful = ['competition_math'
                  ]

    timeout = 3 * 60
    # laion/OIG takes longer
    for num_downloads, dataset in sorted_small_open_english_tasked_datasets:
        data_id = dataset.id
        func = do_one
        args = (data_id, num_downloads)
        kwargs = {}
        with ProcessPoolExecutor(max_workers=1) as executor:
            future = executor.submit(func, *args, **kwargs)
            try:
                future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                print("\n\ndata_id %s timeout\n\n" % data_id, flush=True)
            for child in psutil.Process(os.getpid()).children(recursive=True):
                os.kill(child.pid, signal.SIGINT)
                os.kill(child.pid, signal.SIGTERM)
                os.kill(child.pid, signal.SIGKILL)


def do_one(data_id, num_downloads):
    out_file = "data_%s.parquet" % str(data_id.replace('/', '_'))
    if os.path.isfile(out_file) and os.path.getsize(out_file) > 1024**3:
        return
    try:
        print("Loading data_id %s num_downloads: %s" % (data_id, num_downloads), flush=True)
        avail_list = None
        try:
            data = load_dataset(data_id, 'foobar')
        except Exception as e:
            if 'Available: ' in str(e):
                avail_list = ast.literal_eval(str(e).split('Available:')[1].strip())
            else:
                avail_list = None
        if avail_list is None:
            avail_list = [None]
        print("%s avail_list: %s" % (data_id, avail_list), flush=True)

        for name in avail_list:
            out_file = "data_%s_%s.parquet" % (str(data_id.replace('/', '_')), str(name))
            if os.path.isfile(out_file):
                continue
            data = load_dataset(data_id, name)
            column_names_dict = data.column_names
            column_names = column_names_dict[list(column_names_dict.keys())[0]]
            print("Processing data_id %s num_downloads: %s columns: %s" % (data_id, num_downloads, column_names),
                  flush=True)
            data_dict = data.data
            col_dict = data.num_columns
            first_col = list(col_dict.keys())[0]
            if 'train' in data_dict:
                df = data['train'].to_pandas()
            else:
                df = data[first_col].to_pandas()
            # csv has issues with escaping chars, even for datasets I know I want
            df.to_parquet(out_file, index=False)
    except Exception as e:
        t, v, tb = sys.exc_info()
        ex = ''.join(traceback.format_exception(t, v, tb))
        print("Exception: %s %s" % (data_id, ex), flush=True)
