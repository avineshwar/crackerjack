import re
import random
import string
import os
import time
from app.lib.models.sessions import SessionModel
from app.lib.models.user import UserModel
from app.lib.models.hashcat import HashcatModel, UsedWordlistModel
from app import db
from pathlib import Path
from sqlalchemy import and_, desc
from flask import current_app, send_file


class SessionManager:
    def __init__(self, hashcat, screens, wordlists):
        self.hashcat = hashcat
        self.screens = screens
        self.wordlists = wordlists

    def sanitise_name(self, name):
        return re.sub(r'\W+', '', name)

    def generate_name(self, prefix=''):
        return prefix + ''.join(random.choice(string.ascii_letters + string.digits) for i in range(12))

    def __generate_screen_name(self, user_id, name):
        return str(user_id) + '_' + name;

    def exists(self, user_id, name, active=True):
        return self.__get(user_id, name, active) is not None

    def __get(self, user_id, name, active):
        return SessionModel.query.filter(
            and_(
                SessionModel.user_id == user_id,
                SessionModel.name == name,
                SessionModel.active == active
            )
        ).first()

    def create(self, user_id, name):
        session = self.__get(user_id, name, True)
        if session:
            # Return existing session if there is one.
            return session

        session = SessionModel(
            user_id=user_id,
            name=name,
            active=True,
            screen_name=self.__generate_screen_name(user_id, name)
        )
        db.session.add(session)
        db.session.commit()
        # In order to get the created object, we need to refresh it.
        db.session.refresh(session)

        return session

    def get_data_path(self):
        path = Path(current_app.root_path)
        return os.path.join(str(path.parent), 'data')

    def get_user_data_path(self, user_id, session_id):
        path = os.path.join(self.get_data_path(), str(user_id))
        if session_id > 0:
            path = os.path.join(path, str(session_id))

        if not os.path.isdir(path):
            os.makedirs(path)

        return path

    def get_hashfile_path(self, user_id, session_id):
        return os.path.join(self.get_user_data_path(user_id, session_id), 'hashes.txt')

    def hashfile_exists(self, user_id, session_id):
        path = self.get_hashfile_path(user_id, session_id)
        return os.path.isfile(path)

    def get_potfile_path(self, user_id, session_id):
        return os.path.join(self.get_user_data_path(user_id, session_id), 'hashes.potfile')

    def get_screenfile_path(self, user_id, session_id, name=None):
        if name is None:
            name = 'screen.log'
        return os.path.join(self.get_user_data_path(user_id, session_id), name)

    def get_crackedfile_path(self, user_id, session_id):
        return os.path.join(self.get_user_data_path(user_id, session_id), 'hashes.cracked')

    def can_access(self, user, session_id):
        if user.admin:
            return True

        session = SessionModel.query.filter(
            and_(
                SessionModel.user_id == user.id,
                SessionModel.id == session_id
            )
        ).first()

        return True if session else False

    def get(self, user_id=0, session_id=0):
        conditions = and_(1 == 1)
        if user_id > 0 and session_id > 0:
            conditions = and_(SessionModel.user_id == user_id, SessionModel.id == session_id)
        elif user_id > 0:
            conditions = and_(SessionModel.user_id == user_id)
        elif session_id > 0:
            conditions = and_(SessionModel.id == session_id)

        sessions = SessionModel.query.filter(conditions).order_by(desc(SessionModel.id)).all()

        data = []
        for session in sessions:
            hashcat = self.get_hashcat_settings(session.id)
            hashcat_data_raw = self.get_hashcat_status(session.user_id, session.id)

            item = {
                'id': session.id,
                'name': session.name,
                'screen_name': session.screen_name,
                'user_id': session.user_id,
                'created_at': session.created_at,
                'active': session.active,
                'hashcat': {
                    'configured': True if hashcat else False,
                    'mode': '' if not hashcat else hashcat.mode,
                    'hashtype': '' if not hashcat else hashcat.hashtype,
                    'wordlist': '' if not hashcat else self.wordlists.get_name_from_path(hashcat.wordlist),
                    'wordlist_path': '' if not hashcat else hashcat.wordlist,
                    'rule': '' if not hashcat else os.path.basename(hashcat.rule),
                    'rule_path': '' if not hashcat else hashcat.rule,
                    'mask': '' if not hashcat else hashcat.mask,
                    'increment_min': 0 if not hashcat else hashcat.increment_min,
                    'increment_max': 0 if not hashcat else hashcat.increment_max,
                    'data_raw': hashcat_data_raw,
                    'data': self.process_hashcat_raw_data(hashcat_data_raw, screen_name=session.screen_name),
                    'hashfile': self.get_hashfile_path(session.user_id, session.id),
                    'hashfile_exists': self.hashfile_exists(session.user_id, session.id)
                },
                'user': {
                    'record': UserModel.query.filter(UserModel.id == session.user_id).first()
                }
            }

            data.append(item)

        return data

    def set_hashcat_setting(self, session_id, name, value):
        record = self.get_hashcat_settings(session_id)
        if not record:
            record = self.__create_hashcat_record(session_id)

        if name == 'mode':
            record.mode = value
        elif name == 'hashtype':
            record.hashtype = value
        elif name == 'wordlist':
            # When the wordlist is updated, add the previous wordlist to the "used_wordlists" table.
            if record.wordlist != value and record.wordlist != '':
                used = UsedWordlistModel(
                    session_id=session_id,
                    wordlist=record.wordlist
                )
                db.session.add(used)

            record.wordlist = value
        elif name == 'rule':
            record.rule = value
        elif name == 'mask':
            record.mask = value
        elif name == 'increment_min':
            record.increment_min = value
        elif name == 'increment_max':
            record.increment_max = value

        db.session.commit()

    def get_hashcat_settings(self, session_id):
        return HashcatModel.query.filter(HashcatModel.session_id == session_id).first()

    def __create_hashcat_record(self, session_id):
        record = HashcatModel(
            session_id=session_id
        )

        db.session.add(record)
        db.session.commit()
        # In order to get the created object, we need to refresh it.
        db.session.refresh(record)

        return record

    def backup_screen_log_file(self, user_id, session_id):
        path = self.get_screenfile_path(user_id, session_id)
        if not os.path.isfile(path):
            return True

        new_path = path + '.' + str(int(time.time()))
        os.rename(path, new_path)
        return True

    def is_process_running(self, screen_name):
        screens = self.hashcat.get_process_screen_names()
        return screen_name in screens

    def hashcat_action(self, session_id, action):
        # First get the session.
        session = self.get(session_id=session_id)[0]

        # Make sure the screen is running.
        screen = self.screens.get(session['screen_name'], log_file=self.get_screenfile_path(session['user_id'], session_id))

        if action == 'start':
            command = self.hashcat.build_command_line(
                session['screen_name'],
                int(session['hashcat']['mode']),
                session['hashcat']['mask'],
                session['hashcat']['hashtype'],
                self.get_hashfile_path(session['user_id'], session_id),
                session['hashcat']['wordlist_path'],
                session['hashcat']['rule_path'],
                self.get_crackedfile_path(session['user_id'], session_id),
                self.get_potfile_path(session['user_id'], session_id),
                int(session['hashcat']['increment_min']),
                int(session['hashcat']['increment_max'])
            )

            # Before we start a new session, rename the previous "screen.log" file
            # so that we can determine errors/state easier.
            self.backup_screen_log_file(session['user_id'], session_id)

            # Even though we renamed the file, as it is still open the OS handle will now point to the renamed file.
            # We re-set the screen logfile to the original file.
            screen.set_logfile(self.get_screenfile_path(session['user_id'], session_id))
            screen.execute(command)
        elif action == 'reset':
            # Close the screen.
            screen.quit()

            # Create it again.
            screen = self.screens.get(session['screen_name'], log_file=self.get_screenfile_path(session['user_id'], session_id))
        elif action == 'resume':
            # Hashcat only needs 'r' to resume.
            screen.execute({'r': ''})
        elif action == 'pause':
            # Hashcat only needs 'p' to pause.
            screen.execute({'p': ''})
        elif action == 'stop':
            # Hashcat only needs 'q' to pause.
            screen.execute({'q': ''})
        else:
            return False

        return True

    def get_used_wordlists(self, session_id):
        return UsedWordlistModel.query.filter(UsedWordlistModel.session_id == session_id).all()

    def __remove_escape_characters(self, data):
        # https://stackoverflow.com/questions/14693701/how-can-i-remove-the-ansi-escape-sequences-from-a-string-in-python
        ansi_escape_8bit = re.compile(br'(?:\x1B[@-_]|[\x80-\x9F])[0-?]*[ -/]*[@-~]')
        return ansi_escape_8bit.sub(b'', data)

    def __fix_line_termination(self, data):
        return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")

    def process_hashcat_raw_data(self, raw, screen_name=None):
        # Build base dictionary
        data = {
            'process_state': 0,
            'all_passwords': 0,
            'cracked_passwords': 0,
            'time_remaining': '',
            'estimated_completion_time': '',
            'progress': 0
        }

        # process_state
        # States are:
        #   0   NOT_STARTED
        #   1   RUNNING
        #   2   STOPPED
        #   3   FINISHED
        #   4   PAUSED
        #   5   CRACKED
        #   99  UNKNOWN
        if 'Status' in raw:
            if raw['Status'] == 'Running':
                data['process_state'] = 1
            elif raw['Status'] == 'Quit':
                data['process_state'] = 2
            if raw['Status'] == 'Exhausted':
                data['process_state'] = 3
            elif raw['Status'] == 'Paused':
                data['process_state'] = 4
            elif raw['Status'] == 'Cracked':
                data['process_state'] = 5

        # There's a chance that there's no 'status' output displayed yet. In this case, check if the process is running.
        if data['process_state'] == 0 and screen_name is not None:
            if self.is_process_running(screen_name):
                # Set to running.
                data['process_state'] = 1

        # progress
        if 'Progress' in raw:
            matches = re.findall('\((\d+.\d+)', raw['Progress'])
            if len(matches) == 1:
                data['progress'] = matches[0]

        # passwords
        if 'Recovered' in raw:
            matches = re.findall('(\d+/\d+)', raw['Recovered'])
            if len(matches) > 0:
                passwords = matches[0].split('/')
                if len(passwords) == 2:
                    data['all_passwords'] = int(passwords[1])
                    data['cracked_passwords'] = int(passwords[0])

        # time remaining
        if 'Time.Estimated' in raw:
            matches = re.findall('\((.*)\)', raw['Time.Estimated'])
            if len(matches) == 1:
                data['time_remaining'] = 'Finished' if matches[0] == '0 secs' else matches[0].strip()

        # estimated completion time
        if 'Time.Estimated' in raw:
            matches = re.findall('(.*)\(', raw['Time.Estimated'])
            if len(matches) == 1:
                data['estimated_completion_time'] = matches[0].strip()

        return data

    def get_hashcat_status(self, user_id, session_id):
        screen_log_file = self.get_screenfile_path(user_id, session_id)
        if not os.path.isfile(screen_log_file):
            return {}

        # If we try to read more than the actual size of the file, it will throw an error.
        filesize = os.path.getsize(screen_log_file)
        bytes_to_read = filesize if filesize < 4096 else 4096

        # Read the last 4KB from the screen log file.
        with open(screen_log_file, 'rb') as file:
            file.seek(bytes_to_read * -1, os.SEEK_END)
            stream = file.read()

        # Replace \r\n with \n, and any rebel \r to \n. We only like \n in here!
        # Clean the file from escape characters.
        stream = self.__remove_escape_characters(stream)
        stream = self.__fix_line_termination(stream)

        # Pass to hashcat class to parse and return a dict with all the data.
        data = self.hashcat.parse_stream(stream)

        return data

    def download_file(self, session_id, which_file):
        session = self.get(session_id=session_id)[0]

        save_as = session['name']
        if which_file == 'cracked':
            file = self.get_crackedfile_path(session['user_id'], session_id)
            save_as = save_as + '.cracked'
        elif which_file == 'hashes':
            file = self.get_hashfile_path(session['user_id'], session_id)
            save_as = save_as + '.hashes'
        else:
            return 'Error'

        if not os.path.exists(file):
            return 'Error'

        return send_file(file, attachment_filename=save_as, as_attachment=True)

    def get_running_processes(self):
        sessions = self.get()

        data = {
            'stats': {
                'all': 0,
                'web': 0,
                'ssh': 0
            },
            'commands': {
                'all': [],
                'web': [],
                'ssh': []
            }
        }

        processes = self.hashcat.get_running_processes_commands()

        data['stats']['all'] = len(processes)
        data['commands']['all'] = processes

        for process in processes:
            name = self.hashcat.extract_session_from_process(process)
            found = False
            for session in sessions:
                if session['screen_name'] == name:
                    found = True
                    break

            key = 'web' if found else 'ssh'
            data['stats'][key] = data['stats'][key] + 1
            data['commands'][key].append(process)

        return data

    def save_hashes(self, user_id, session_id, hashes):
        save_as = self.get_hashfile_path(user_id, session_id)
        with open(save_as, 'w') as f:
            f.write(hashes)

        return True