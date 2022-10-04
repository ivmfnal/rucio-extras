# -*- coding: utf-8 -*-
# Copyright European Organization for Nuclear Research (CERN) since 2012
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from rucio.common.config import config_get_bool
from rucio.common.types import InternalAccount, InternalScope
from rucio.common.utils import generate_uuid as uuid
from rucio.core.account import get_usage
from rucio.core.account_limit import set_local_account_limit
from rucio.core.did import add_did, attach_dids, detach_dids
from rucio.core.lock import get_replica_locks, get_dataset_locks
from rucio.core.rse import add_rse_attribute, get_rse_id
from rucio.core.rule import add_rule, get_rule
from rucio.daemons.abacus.account import account_update
from rucio.daemons.judge.evaluator import re_evaluator
from rucio.db.sqla.constants import DIDType
from rucio.db.sqla.models import UpdatedDID
from rucio.db.sqla.session import transactional_session
from rucio.tests.common_server import get_vo
from rucio.tests.test_rule import create_files, tag_generator

class TestJudgeEvaluator(object):

    def __init__(self):
        if config_get_bool('common', 'multi_vo', raise_exception=False, default=False):
            self.vo = {'vo': get_vo()}
        else:
            self.vo = {}

        @transactional_session
        def __cleanup_updated_dids(session=None):
            session.query(UpdatedDID).delete()

        __cleanup_updated_dids()

        # Add test RSE
        self.rse1 = 'MOCK'
        self.rse3 = 'MOCK3'
        self.rse4 = 'MOCK4'
        self.rse5 = 'MOCK5'

        self.rse1_id = get_rse_id(rse=self.rse1, **self.vo)
        self.rse3_id = get_rse_id(rse=self.rse3, **self.vo)
        self.rse4_id = get_rse_id(rse=self.rse4, **self.vo)
        self.rse5_id = get_rse_id(rse=self.rse5, **self.vo)

        # Add Tags
        self.T1 = tag_generator()
        self.T2 = tag_generator()
        add_rse_attribute(self.rse1_id, self.T1, True)
        add_rse_attribute(self.rse3_id, self.T1, True)
        add_rse_attribute(self.rse4_id, self.T2, True)
        add_rse_attribute(self.rse5_id, self.T1, True)

        # Add fake weights
        add_rse_attribute(self.rse1_id, "fakeweight", 10)
        add_rse_attribute(self.rse3_id, "fakeweight", 0)
        add_rse_attribute(self.rse4_id, "fakeweight", 0)
        add_rse_attribute(self.rse5_id, "fakeweight", 0)

        # Add quota
        self.jdoe = InternalAccount('jdoe', **self.vo)
        self.root = InternalAccount('root', **self.vo)
        set_local_account_limit(self.jdoe, self.rse1_id, -1)
        set_local_account_limit(self.jdoe, self.rse3_id, -1)
        set_local_account_limit(self.jdoe, self.rse4_id, -1)
        set_local_account_limit(self.jdoe, self.rse5_id, -1)

        set_local_account_limit(self.root, self.rse1_id, -1)
        set_local_account_limit(self.root, self.rse3_id, -1)
        set_local_account_limit(self.root, self.rse4_id, -1)
        set_local_account_limit(self.root, self.rse5_id, -1)

    def test_judge_add_dataset_to_container(self):
        """ JUDGE EVALUATOR: Test the judge when adding dataset to container"""
        scope = InternalScope('mock', **self.vo)
        files = create_files(3, scope, self.rse1_id)
        dataset = 'dataset_' + str(uuid())
        add_did(scope, dataset, DIDType.DATASET, self.jdoe)
        attach_dids(scope, dataset, files, self.jdoe)

        parent_container = 'dataset_' + str(uuid())
        add_did(scope, parent_container, DIDType.CONTAINER, self.jdoe)
        # Add a first rule to the DS
        add_rule(dids=[{'scope': scope, 'name': parent_container}], account=self.jdoe, copies=2, rse_expression=self.T1, grouping='DATASET', weight=None, lifetime=None, locked=False, subscription_id=None)
        attach_dids(scope, parent_container, [{'scope': scope, 'name': dataset}], self.jdoe)
        # Fake judge
        re_evaluator(once=True, did_limit=1000)

        # Check if the Locks are created properly
        for file in files:
            assert(len(get_replica_locks(scope=file['scope'], name=file['name'])) == 2)

        # Check if the DatasetLocks are created properly
        dataset_locks = [lock for lock in get_dataset_locks(scope=scope, name=dataset)]
        assert(len(dataset_locks) == 2)

    def test_judge_dataset_grouping_all(self):
        """ JUDGE EVALUATOR: Test the judge when adding dataset to existing container with ALL grouping"""

        # create a container
        scope = InternalScope('mock', **self.vo)
        parent_container = 'container_' + str(uuid())
        add_did(scope, parent_container, DIDType.CONTAINER, self.jdoe)

        # create a dataset, populate it with an "existing" file and declare that they reside in the T1 RSE
        files = create_files(1, scope, self.rse1_id)        # rse1 has T1 tag
        dataset1 = 'dataset_' + str(uuid())
        add_did(scope, dataset1, DIDType.DATASET, self.jdoe)
        attach_dids(scope, dataset1, files, self.jdoe)

        # attach the dataset to the container
        attach_dids(scope, parent_container, [{'scope': scope, 'name': dataset1}], self.jdoe)

        # add rule to copy everything in this container to T1, use grouping=ALL
        add_rule(dids=[{'scope': scope, 'name': parent_container}],
                 account=self.jdoe,
                 copies=1, rse_expression=self.T1, grouping='ALL',
                 weight=None, lifetime=None, locked=False, subscription_id=None
                 )

        re_evaluator(once=True, did_limit=1000)         # to clear any history

        # create another dataset, populate it with "new" files and declare that they reside in a T2 RSE
        new_files = create_files(5, scope, self.rse4_id)        # rse4 has T2 tag
        dataset2 = 'dataset_' + str(uuid())
        add_did(scope, dataset2, DIDType.DATASET, self.jdoe)
        attach_dids(scope, dataset2, new_files, self.jdoe)

        # attach the new dataset to the container
        attach_dids(scope, parent_container, [{'scope': scope, 'name': dataset2}], self.jdoe)

        # re-run the evaluator
        re_evaluator(once=True, did_limit=1000)

        # check if the evaluator created locks to move the new files to the same RSE where old files are
        for file in new_files:
            locks = get_replica_locks(scope=file['scope'], name=file['name'])
            assert len(locks) == 1
            lock = locks[0]
            assert lock["rse_id"] == self.rse1_id

if __name__ == "__main__":
    t = TestJudgeEvaluator()
    t.test_judge_add_dataset_to_container()
    #t.test_judge_dataset_grouping_all()