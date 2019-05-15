#!/usr/bin/env python3

# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Script to create SSL splits from a dataset.
"""

from collections import defaultdict
import json
import os

from absl import app
from absl import flags
from libml import utils
import numpy as np
import tensorflow as tf
from tqdm import trange, tqdm

from libml.data import DATA_DIR

flags.DEFINE_integer('seed', 0, 'Random seed to use, 0 for no shuffling.')
flags.DEFINE_integer('size', 0, 'Size of labelled set.')

FLAGS = flags.FLAGS


def get_class(serialized_example):
    return tf.parse_single_example(serialized_example, features={'label': tf.FixedLenFeature([], tf.int64)})['label']


def main(argv):
    assert FLAGS.size
    argv.pop(0)
    if any(not os.path.exists(f) for f in argv[1:]):
        raise FileNotFoundError(argv[1:])
    target = '%s.%d@%d' % (argv[0], FLAGS.seed, FLAGS.size)
    if os.path.exists(target):
        raise FileExistsError('For safety overwriting is not allowed', target)
    input_files = argv[1:]
    count = 0
    id_class = []
    class_id = defaultdict(list)
    print('Computing class distribution')
    dataset = tf.data.TFRecordDataset(input_files)
    dataset = dataset.map(get_class, 4).batch(1 << 10)
    it = dataset.make_one_shot_iterator().get_next()
    try:
        with tf.Session() as session, tqdm(leave=False) as t:
            while 1:
                old_count = count
                for i in session.run(it):
                    id_class.append(i)
                    class_id[i].append(count)
                    count += 1
                t.update(count - old_count)
    except tf.errors.OutOfRangeError:
        pass
    print('%d records found' % count)

    nclass = 10
    train_stats = np.array([len(class_id[i]) for i in range(nclass)], np.float64)
    train_stats /= train_stats.max()
    assert 'svhn' in argv[1], 'This script is only for private svhn.'
    print('  Stats', ' '.join(['%.2f' % (100 * x) for x in train_stats]))

    assert min(class_id.keys()) == 0 and max(class_id.keys()) == 255  # Unlabeled stored as -1 = 255 (uint8)
    class_id = [np.array(class_id[i], dtype=np.int64) for i in range(nclass)]
    if FLAGS.seed:
        np.random.seed(FLAGS.seed)
        for i in range(nclass):
            np.random.shuffle(class_id[i])
    # Distribute labels to match the input distribution.
    npos = np.zeros(nclass, np.int64)
    label = []
    for i in range(FLAGS.size):
        c = np.argmax(train_stats - npos / max(npos.max(), 1))
        label.append(class_id[c][npos[c]])
        npos[c] += 1

    del npos, class_id
    label = frozenset([int(x) for x in label])
    if 'stl10' in argv[1] and FLAGS.size == 1000:
        data = open(os.path.join(DATA_DIR, 'stl10_fold_indices.txt'), 'r').read()
        label = frozenset(list(map(int, data.split('\n')[FLAGS.seed].split())))

    print('Creating split in %s' % target)
    npos = np.zeros(nclass, np.int64)

    class_data = [[] for _ in range(256)]
    unlabel = []
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with tf.python_io.TFRecordWriter(target + '-label.tfrecord') as writer_label, tf.python_io.TFRecordWriter(
            target + '-unlabel.tfrecord') as writer_unlabel:
        pos, loop = 0, trange(count, desc='Writing records')
        for input_file in input_files:
            for record in tf.python_io.tf_record_iterator(input_file):
                if pos in label:
                    writer_label.write(record)
                else:
                    try:
                        class_data[id_class[pos]].append((pos, record))
                    except:
                        print(id_class[pos])
                    while True:
                        c = np.argmax(train_stats - npos / max(npos.max(), 1))
                        if class_data[c]:
                            p, v = class_data[c].pop(0)
                            unlabel.append(p)
                            writer_unlabel.write(v)
                            npos[c] += 1
                        else:
                            break
                pos += 1
                loop.update()
        for remain in class_data:
            for p, v in remain:
                unlabel.append(p)
                writer_unlabel.write(v)
        loop.close()
    with open(target + '-map.json', 'w') as writer:
        writer.write(json.dumps(
            dict(label=sorted(label), unlabel=unlabel), indent=2, sort_keys=True))


if __name__ == '__main__':
    utils.setup_tf()
    app.run(main)
