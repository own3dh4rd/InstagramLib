#!/usr/bin/python3
import hashlib
import re
import json

import requests
from requests.exceptions import *
from time import sleep


# Exception classes
class InstagramException(Exception):
    pass


class InternetException(InstagramException):
    def __init__(self, e):
        self.error = e

    def __getattr__(self, name):
        return self.error.__getattribute__(name)

    def __str__(self):
        return "Error by connection with Instagram to '{0}' with response code '{1}'".format(
            self.error.request.url, self.error.response.status_code)


class AuthException(Exception):
    def __init__(self, login):
        super().__init__("Cannot auth user with username '{0}'".format(login))


class UnexpectedResponse(InstagramException):
    def __init__(self, url, data=None):
        self.url = url
        self.data = data

        message = "Get unexpected response from '{}'".format(url)

        if data:
            message = "{0} with data: {1}".format(message, str(data))

        super().__init__(message)


class NotUpdatedElement(InstagramException):
    def __init__(self, element, argument):
        super().__init__(
            "Element '{0}' haven't argument {1}. Please, update this element".format(
                element.__repr__(), argument))


# Exception struct
class ExceptionTree:
    def __init__(self):
        self.__tree__ = {
            'action': lambda exception, *args, **kwargs: (args, kwargs),
            'branch': {},
        }

    def __getitem__(self, key):
        # Check data
        if not issubclass(key, Exception):
            raise TypeError("Key must be Exception type")
        return self.__search__(key)['action']

    def __setitem__(self, key, value):
        # Check data
        if not issubclass(key, Exception):
            raise TypeError("Key must be Exception type")
        if not callable(value):
            raise TypeError("Value must be function")

        item, exists = self.__search__(key, False)
        if exists:
            item['action'] = value
        else:
            item['branch'][key] = {'branch': {}, 'action': value}

    def __search__(self, exception, get=True):
        # Check data
        if not issubclass(exception, Exception):
            raise TypeError("'exception' must be Exception type")

        # Search
        current = self.__tree__
        while True:
            for key, value in current['branch'].items():
                if key == exception:
                    if not get:
                        return value, True
                    return value
                elif issubclass(exception, key):
                    current = value
                    break
            else:
                if not get:
                    return current, False
                return current
            continue


class Agent:
    # Anonymous session
    __session__ = requests.Session()
    repeats = 1

    def exceptionDecorator(func):
        def wrapper(self, *args, **kwargs):
            count = 0
            while True:
                count += 1
                try:
                    return func(self, *args, **kwargs)
                except Exception as e:
                    if count < Agent.repeats:
                        args, kwargs = self.exception_actions[e.__class__](e,
                                                                           *args,
                                                                           **kwargs)
                    else:
                        raise e

        return wrapper

    def __http_error_action__(exception, *args, **kwargs):
        if exception.status_code in (403, 429):
            sleep(2)
            return (args, kwargs)
        raise exception

    exception_actions = ExceptionTree()
    exception_actions[HTTPError] = __http_error_action__

    @exceptionDecorator
    def __update__(self, obj=None, settings={}):
        # Checks and set data
        if not isinstance(settings, dict):
            raise TypeError("'settings' must be dict type")

        if isinstance(obj, Account):
            query = "https://www.instagram.com/{0}".format(obj.login)
        elif isinstance(obj, Media):
            query = "https://www.instagram.com/p/{0}".format(obj.code)
        elif isinstance(obj, Location):
            query = "https://www.instagram.com/explore/locations/{0}".format(
                obj.id)
        elif isinstance(obj, Tag):
            query = "https://www.instagram.com/explore/tags/{0}".format(
                obj.name)
        else:
            raise TypeError("obj must be Account, Media, Location or Tag")

        # Request
        response = self.__send_get_request__(query, **settings)

        # Parsing info
        try:
            match = re.search(
                r"<script[^>]*>\s*window._sharedData\s*=\s*((?!<script>).*)\s*;\s*</script>",
                response.text,
            )
            data = json.loads(match.group(1))
            if 'rhx_gis' in data:
                self.rhx_gis = data['rhx_gis']
            if 'csrf_token' in data['config']:
                self.csrf_token = data['config']['csrf_token']
            data = data['entry_data']
            if isinstance(obj, Account):
                data = data['ProfilePage'][0]['graphql']['user']
            elif isinstance(obj, Media):
                data = data['PostPage'][0]['graphql']['shortcode_media']
            elif isinstance(obj, Location):
                data = data['LocationsPage'][0]['graphql']['location']
            elif isinstance(obj, Tag):
                data = data['TagPage'][0]['graphql']['hashtag']
            obj.__setDataFromJSON__(data)
            return data
        except (AttributeError, KeyError, ValueError):
            raise UnexpectedResponse(response.url, response.text)

    @exceptionDecorator
    def getMedia(self, obj, after=None, count=12, settings={},
                 limit=12):
        # Check data
        if not isinstance(count, int):
            raise TypeError("'count' must be int type")

        data = self.__update__(obj, settings)
        media_list = []
        stop = False

        # Parse first request
        if not after:
            try:
                if not isinstance(settings, dict):
                    raise TypeError("'settings' must be dict type")
                if isinstance(obj, Account):
                    data = data['edge_owner_to_timeline_media']
                elif isinstance(obj, Tag):
                    data = data['edge_location_to_media']
                elif isinstance(obj, Location):
                    data = data['edge_hashtag_to_media']
                else:
                    raise TypeError(
                        "obj must be Account, Media, Location or Tag")
                for media in data['edges']:
                    media = media['node']
                    m = Media(media['shortcode'])
                    m.__setDataFromJSON__(media)
                    if isinstance(obj, Account):
                        m.likes_count = media['edge_media_preview_like'][
                            'count']
                        m.owner = obj
                    else:
                        m.likes_count = media['edge_liked_by']
                    obj.media.add(m)
                    media_list.append(m)
                if len(data['edges']) < count and data['page_info'][
                    'has_next_page']:
                    count = count - len(data['edges'])
                else:
                    stop = True
                if data['page_info']['has_next_page']:
                    after = data['page_info']['end_cursor']
                else:
                    after = None
            except (ValueError, KeyError):
                raise UnexpectedResponse(response.url, response.text)

        # Set params
        if not 'params' in settings:
            settings['params'] = {
                'query_hash': "42323d64886122307be10013ad2dcc44",
            }

        while not stop:
            # Set params
            data = {'after': after}
            if limit < count:
                data['first'] = limit
            else:
                data['first'] = count
            if isinstance(obj, Tag):
                data['name'] = 'tag_name'
                data['name_value'] = obj.name
            else:
                data['name'] = 'id'
                data['name_value'] = obj.id
            settings['params'][
                'variables'] = '{{"{name}":"{name_value}","first":{first},"after":"{after}"}}'.format(
                **data)
            # Set GIS header
            settings['headers'] = {
                'X-Instagram-GIS': hashlib.md5('{0}:{1}:{2}'.format(
                    self.rhx_gis,
                    self.csrf_token,
                    settings['params']['variables'],
                ).encode('utf-8')).hexdigest(),
            }

            # Send request
            response = self.__send_get_request__(
                "https://www.instagram.com/graphql/query/",
                **settings,
            )

            # Parsing info
            try:
                if isinstance(obj, Account):
                    data = response.json()['data']['user'][
                        'edge_owner_to_timeline_media']
                elif isinstance(obj, Location):
                    data = response.json()['data']['location'][
                        'edge_location_to_media']
                elif isinstance(obj, Tag):
                    data = response.json()['data']['hashtag'][
                        'edge_hashtag_to_media']
                for media in data['edges']:
                    media = media['node']
                    m = Media(media['shortcode'])
                    m.__setDataFromJSON__(media)
                    if isinstance(obj, Account):
                        m.likes_count = media['edge_media_preview_like'][
                            'count']
                        m.owner = obj
                    else:
                        m.likes_count = media['edge_liked_by']
                    obj.media.add(m)
                    media_list.append(m)
                if len(data['edges']) < count and data['page_info'][
                    'has_next_page']:
                    count = count - len(data['edges'])
                else:
                    stop = True
                if data['page_info']['has_next_page']:
                    after = data['page_info']['end_cursor']
                else:
                    after = None
            except (ValueError, KeyError):
                raise UnexpectedResponse(response.url, response.text)
        return media_list, after

    @exceptionDecorator
    def getLikes(self, media, settings={}):
        # Check data
        if not isinstance(settings, dict):
            raise TypeError("'settings' must be dict type")
        if not isinstance(media, Media):
            raise TypeError("'media' must be Media type")

        data = self.__update__(media, settings)
        likes_list = []
        # Parse first request
        try:
            data = data['edge_media_preview_like']
            for edge in data['edges']:
                edge = edge['node']
                account = Account(edge['username'])
                account.id = edge['id']
                account.profile_pic_url = edge['profile_pic_url']
                if 'is_verified' in edge:
                    account.is_verified = edge['is_verified']
                if 'full_name' in edge:
                    account.full_name = edge['full_name']
                media.likes.add(account)
                likes_list.append(account)
        except (ValueError, KeyError):
            raise UnexpectedResponse(response.url, response.text)
        return likes_list, None

    @exceptionDecorator
    def getComments(self, media, after=None, count=35, settings={},
                    limit=1000):
        # Check data
        if not isinstance(settings, dict):
            raise TypeError("'settings' must be dict type")
        if not isinstance(count, int):
            raise TypeError("'count' must be int type")
        if not isinstance(media, Media):
            raise TypeError("'media' must be Media type")

        data = self.__update__(media, settings)
        comments_list = []

        if not after:
            try:
                data = data['edge_media_to_comment']
                for comment in data['edges']:
                    comment = comment['node']
                    c = Comment(
                        comment['id'],
                        media=media,
                        owner=Account(comment['owner']['username']),
                        text=comment['text'],
                        created_at=comment['created_at'],
                    )
                    media.comments.add(c)
                    comments_list.append(c)
                if len(data['edges']) < count and data['page_info'][
                    'has_next_page']:
                    count = count - len(data['edges'])
                    after = data['page_info']['end_cursor']
                else:
                    stop = True
            except (ValueError, KeyError):
                raise UnexpectedResponse(response.url, response.text)

        # Set params
        if not 'params' in settings:
            settings['params'] = {
                'query_hash': "33ba35852cb50da46f5b5e889df7d159",
            }

        while not stop:
            # Set params
            data = {'after': after, 'code': media.code}
            if limit < count:
                data['first'] = limit
            else:
                data['first'] = count
            settings['params']['variables'] = \
                '{{"shortcode":"{code}","first":{first},"after":"{after}"}}'.format(
                    **data)
            # Set GIS header
            settings['headers'] = {
                'X-Instagram-GIS': hashlib.md5('{0}:{1}:{2}'.format(
                    self.rhx_gis,
                    self.csrf_token,
                    settings['params']['variables'],
                ).encode('utf-8')).hexdigest(),
            }

            # Request for get info
            response = self.__send_get_request__(
                "https://www.instagram.com/graphql/query/",
                **settings,
            )

            # Parsing info
            try:
                data = response.json()['data']['shortcode_media'][
                    'edge_media_to_comment']
                media.comments_count = data['count']
                for comment in data['edges']:
                    comment = comment['node']
                    c = Comment(
                        id=comment['id'],
                        media=media,
                        owner=Account(comment['owner']['username']),
                        text=comment['text'],
                        data=comment['created_at'],
                    )
                    media.comments.add(c)
                    comments_list.append(c)
                if len(data['edges']) < count and data['page_info'][
                    'has_next_page']:
                    count = count - len(data['edges'])
                else:
                    stop = True
                if data['page_info']['has_next_page']:
                    after = data['page_info']['end_cursor']
                else:
                    after = None
            except (ValueError, KeyError):
                raise UnexpectedResponse(response.url, response.text)
        return comments_list, after

    def __send_get_request__(self, *args, raise_for_status=True, **kwargs):
        count = 0
        while True:
            count += 1
            try:
                response = self.__session__.get(*args, **kwargs)
                if raise_for_status:
                    response.raise_for_status()
                return response
            except Exception as e:
                if count < self.repeats:
                    args, kwargs = self.exception_actions[e.__class__](e, *args,
                                                                       **kwargs)
                else:
                    raise InternetException(e)

    def __send_post_request__(self, *args, raise_for_status=True, **kwargs):
        count = 0
        while True:
            count += 1
            try:
                response = self.__session__.post(*args, **kwargs)
                if raise_for_status:
                    response.raise_for_status()
                return response
            except Exception as e:
                if count < self.repeats:
                    args, kwargs = self.exception_actions[e.__class__](e, *args,
                                                                       **kwargs)
                else:
                    raise InternetException(e)


# Account class
class Account:
    def __init__(self, login):
        self.id = None
        self.login = login
        self.full_name = None
        self.profile_pic_url = None
        self.profile_pic_url_hd = None
        self.fb_page = None
        self.biography = None
        self.follows_count = None
        self.followers_count = None
        self.media_count = None
        self.is_private = None
        self.is_verified = None
        self.country_block = None
        # Lists
        self.media = set()
        self.follows = set()
        self.followers = set()

    def __setDataFromJSON__(self, data):
        self.id = data['id']
        self.full_name = data['full_name']
        self.profile_pic_url = data['profile_pic_url']
        self.profile_pic_url_hd = data['profile_pic_url_hd']
        self.fb_page = data['connected_fb_page']
        self.biography = data['biography']
        self.follows_count = data['edge_follow']['count']
        self.followers_count = data['edge_followed_by']['count']
        self.media_count = data['edge_owner_to_timeline_media']['count']
        self.is_private = data['is_private']
        self.is_verified = data['is_verified']
        self.country_block = data['country_block']


class AgentAccount(Account, Agent):
    @Agent.exceptionDecorator
    def __init__(self, login, password, settings={}):
        super().__init__(login)
        if not isinstance(settings, dict):
            raise TypeError("'settings' must be dict type")
        # Request for get start page for get CSRFToken
        response = self.__send_get_request__(
            "https://www.instagram.com/",
            **settings
        )
        # Create login data structure
        data = {
            "username": self.login,
            "password": password,
        }
        # Create headers
        headers = {
            "X-CSRFToken": response.cookies["csrftoken"],
            "referer": "https://www.instagram.com/",
        }
        # Login request
        response = self.__send_post_request__(
            "https://www.instagram.com/accounts/login/ajax/",
            data=data,
            headers=headers,
            **settings,
        )

        # Parse response info
        try:
            data = response.json()
            if data['status'] == 'fail' or not data['authenticated']:
                raise AuthException(self.login)
        except (ValueError, KeyError):
            raise UnexpectedResponse(response.url, response.text)

    @Agent.exceptionDecorator
    def getMedia(self, obj, after=None, count=12, settings={},
                 limit=1000):
        return super().getMedia(obj, after, count, settings, limit)

    @Agent.exceptionDecorator
    def getLikes(self, media, after=None, count=20, settings={},
                 limit=1000):
        # Check data
        if not isinstance(media, Media):
            raise TypeError("'media' must be Media type")
        if not isinstance(count, int):
            raise TypeError("'count' must be int type")
        if not isinstance(settings, dict):
            raise TypeError("'settings' must be dict type")
        if not isinstance(limit, int):
            raise TypeError("'limit' must be int type")

        # Update media
        self.__update__(media, settings)
        likes_list = []
        stop = False

        # Set data
        # Set params
        if not 'params' in settings:
            settings['params'] = {
                'query_hash': "1cb6ec562846122743b61e492c85999f",
                'variables': '{{"shortcode":"{shortcode}","first":{first}}}',
            }

        while not stop:
            data = {}
            if after:
                data['after'] = after

            if limit < count:
                data['first'] = limit
            else:
                data['first'] = count
            data['shortcode'] = media.code
            settings['params']['variables'] = \
                settings['params']['variables'].format(**data)

            # Request for get info
            response = self.__send_get_request__(
                "https://www.instagram.com/graphql/query/",
                **settings,
            )

            # Parsing info
            try:
                data = response.json()['data']['shortcode_media'][
                    'edge_liked_by']
                media.likes_count = data['count']
                for edge in data['edges']:
                    account = Account(edge['node']['username'])
                    account.id = edge['node']['id']
                    account.profile_pic_url = edge['node']['profile_pic_url']
                    account.is_verified = edge['node']['is_verified']
                    account.full_name = edge['node']['full_name']
                    media.likes.add(account)
                    likes_list.append(account)
                if len(data['edges']) < count and data['page_info'][
                    'has_next_page']:
                    count = count - len(data['edges'])
                    settings['params']['variables'] = \
                        '{{"shortcode":"{shortcode}","first":{first},"after":"{after}"}}'
                else:
                    stop = True
                if data['page_info']['has_next_page']:
                    after = data['page_info']['end_cursor']
                else:
                    after = None
            except (ValueError, KeyError):
                raise UnexpectedResponse(response.url, response.text)

        return likes_list, after

    @Agent.exceptionDecorator
    def getFollows(self, account=None, after=None, count=20, settings={},
                   limit=1000):
        # Check set and data
        if not account:
            account = self
        if not isinstance(settings, dict):
            raise TypeError("'settings' must be dict type")
        if not isinstance(count, int):
            raise TypeError("'count' must be int type")
        if not isinstance(account, Account):
            raise TypeError("'account' must be Account type")

        # Update account
        self.__update__(account, settings)
        follows_list = []
        stop = False

        # Set data
        # Set params
        if not 'params' in settings:
            settings['params'] = {
                'query_hash': "58712303d941c6855d4e888c5f0cd22f",
                'variables': '{{"id":"{id}","first":{first}}}',
            }

        while not stop:
            data = {}
            if after:
                data['after'] = after
            if limit < count:
                data['first'] = limit
            else:
                data['first'] = count
            data['id'] = account.id
            settings['params']['variables'] = \
                settings['params']['variables'].format(**data)

            # Request for get info
            response = self.__send_get_request__(
                "https://www.instagram.com/graphql/query/",
                **settings,
            )

            # Parsing info
            try:
                data = response.json()['data']['user']['edge_follow']
                account.follows_count = data['count']
                for follow in data['edges']:
                    follow = follow['node']
                    a = Account(follow['username'])
                    a.id = follow['id']
                    a.profile_pic_url = follow['profile_pic_url']
                    a.is_verified = follow['is_verified']
                    a.full_name = follow['full_name']
                    account.follows.add(a)
                    follows_list.append(a)
                if len(data['edges']) < count and data['page_info'][
                    'has_next_page']:
                    count = count - len(data['edges'])
                    settings['params']['variables'] = \
                        '{{"id":"{id}","first":{first},"after":"{after}"}}'
                else:
                    stop = True
                if data['page_info']['has_next_page']:
                    after = data['page_info']['end_cursor']
                else:
                    after = None
            except (ValueError, KeyError):
                raise UnexpectedResponse(response.url, response.text)

        return follows_list, after

    @Agent.exceptionDecorator
    def getFollowers(self, account=None, after=None, count=20,
                     settings={}, limit=1000):
        # Check set and data
        if not account:
            account = self
        if not isinstance(settings, dict):
            raise TypeError("'settings' must be dict type")
        if not isinstance(count, int):
            raise TypeError("'count' must be int type")
        if not isinstance(account, Account):
            raise TypeError("'account' must be Account type")

        # Update account
        self.__update__(account, settings)
        followers_list = []
        stop = False

        # Set data
        # Set params
        if not 'params' in settings:
            settings['params'] = {
                'query_hash': "37479f2b8209594dde7facb0d904896a",
                'variables': '{{"id":"{id}","first":{first}}}',
            }

        while not stop:
            data = {}
            if after:
                data['after'] = after
            if limit < count:
                data['first'] = limit
            else:
                data['first'] = count
            data['id'] = account.id
            settings['params']['variables'] = \
                settings['params']['variables'].format(**data)

            # Request for get info
            response = self.__send_get_request__(
                "https://www.instagram.com/graphql/query/",
                **settings,
            )

            # Parsing info
            try:
                data = response.json()['data']['user']['edge_followed_by']
                account.followers_count = data['count']
                for follower in data['edges']:
                    follower = follower['node']
                    a = Account(follower['username'])
                    a.id = follower['id']
                    a.profile_pic_url = follower['profile_pic_url']
                    a.is_verified = follower['is_verified']
                    a.full_name = follower['full_name']
                    account.followers.add(a)
                    followers_list.append(a)
                if len(data['edges']) < count and data['page_info'][
                    'has_next_page']:
                    count = count - len(data['edges'])
                    settings['params']['variables'] = \
                        '{{"id":"{id}","first":{first},"after":"{after}"}}'
                else:
                    stop = True
                if data['page_info']['has_next_page']:
                    after = data['page_info']['end_cursor']
                else:
                    after = None
            except (ValueError, KeyError):
                raise UnexpectedResponse(response.url, response.text)
        return followers_list, after

    def feed(self, count=12, settings={}):
        # Check set and data
        if not isinstance(settings, dict):
            raise TypeError("'settings' must be dict type")
        if not isinstance(count, int):
            raise TypeError("'count' must be int type")

        # Set data
        feed = []
        stop = False

        # Request for get info
        response = self.__send_get_request__(
            "https://www.instagram.com/?__a=1",
            **settings,
        )

        # Parsing info
        try:
            data = response.json()['graphql']['user']['edge_web_feed_timeline']
            cursor = data['page_info']['end_cursor']
            data = data['edges']
            for edge in data:
                edge = edge['node']
                media = Media(edge['shortcode'])
                media.id = int(edge['id'])
                if edge['edge_media_to_caption']['edges']:
                    media.caption = \
                        edge['edge_media_to_caption']['edges'][0]['node'][
                            'text']
                media.owner = Account(edge['owner']['username'])
                media.owner.id = int(edge['owner']['id'])
                media.owner.full_name = edge['owner']['full_name']
                media.owner.profile_pic_url = edge['owner']['profile_pic_url']
                media.owner.is_private = edge['owner']['is_private']
                media.date = edge['taken_at_timestamp']
                if edge['location']:
                    media.location = Location(edge['location']['id'])
                media.likes_count = edge['edge_media_preview_like']['count']
                media.comments_count = edge['edge_media_to_comment']['count']
                media.comments_disabled = edge['comments_disabled']
                media.is_video = edge['is_video']
                if 'video_url' in edge:
                    media.video_url = edge['video_url']
                media.display_url = edge['display_url']
                media.dimensions = (
                    edge['dimensions']['width'], edge['dimensions']['height'])
                feed.append(media)
        except (ValueError, KeyError):
            raise UnexpectedResponse(response.url, response.text)

        # Set data
        stop = (count <= len(feed))
        count -= len(feed)
        data = {'query_id': 17842794232208280,
                'variables': '{"fetch_media_item_count":' + str(
                    count) + ',"fetch_media_item_cursor":"' + cursor + '","fetch_comment_count":4,"fetch_like":10,"has_stories":false}'}
        if 'params' in settings:
            settings['params'].update(data)
        else:
            settings['params'] = data

        while not stop:
            # Request for get info
            response = self.__send_get_request__(
                "https://www.instagram.com/graphql/query/",
                **settings,
            )

            # Parsing info
            try:
                data = response.json()['data']['user']['edge_web_feed_timeline']
                cursor = data['page_info']['end_cursor']
                for edge in data['edges']:
                    edge = edge['node']
                    media = Media(edge['shortcode'])
                    media.id = int(edge['id'])
                    if edge['edge_media_to_caption']['edges']:
                        media.caption = \
                            edge['edge_media_to_caption']['edges'][0]['node'][
                                'text']
                    media.owner = Account(edge['owner']['username'])
                    media.owner.id = int(edge['owner']['id'])
                    media.owner.full_name = edge['owner']['full_name']
                    media.owner.profile_pic_url = edge['owner'][
                        'profile_pic_url']
                    media.owner.is_private = edge['owner']['is_private']
                    media.date = edge['taken_at_timestamp']
                    if edge['location']:
                        media.location = Location(edge['location']['id'])
                    media.likes_count = edge['edge_media_preview_like']['count']
                    media.comments_count = edge['edge_media_to_comment'][
                        'count']
                    media.comments_disabled = edge['comments_disabled']
                    media.is_video = edge['is_video']
                    if 'video_url' in edge:
                        media.video_url = edge['video_url']
                    media.display_url = edge['display_url']
                    media.dimensions = (
                        edge['dimensions']['width'],
                        edge['dimensions']['height'])
                    feed.append(media)
                # Recursive calling method if not all elements was loading
                if len(data['edges']) < count and data['page_info'][
                    'has_next_page']:
                    count = count - len(data['edges'])
                    settings['params'][
                        'variables'] = '{"fetch_media_item_count":' + str(
                        count) + ',"fetch_media_item_cursor":"' + \
                                       data['page_info'][
                                           'end_cursor'] + '","fetch_comment_count":4,"fetch_like":10,"has_stories":false}'
                else:
                    stop = True
            except (ValueError, KeyError):
                raise UnexpectedResponse(response.url, response.text)
        return feed

    @Agent.exceptionDecorator
    def like(self, media, settings={}):
        # Check data
        if not isinstance(media, Media):
            raise TypeError("'media' must be Media type")
        if not isinstance(settings, dict):
            raise TypeError("'settings' must be dict type")
        if media.id == None:
            raise NotUpdatedElement(media, 'id')

        response = self.__action_handler__(
            referer="https://www.instagram.com/p/{0}/".format(media.code),
            url="https://www.instagram.com/web/likes/{0}/like/".format(
                media.id),
        )

        # Parsing
        try:
            if response.json()['status'] == 'ok':
                return True
            else:
                return False
        except (ValueError, KeyError):
            raise UnexpectedResponse(response.url, response.text)

    @Agent.exceptionDecorator
    def unlike(self, media, settings={}):
        # Check data
        if not isinstance(media, Media):
            raise TypeError("'media' must be Media type")
        if not isinstance(settings, dict):
            raise TypeError("'settings' must be dict type")
        if media.id == None:
            raise NotUpdatedElement(media, 'id')

        # Request
        response = self.__action_handler__(
            referer="https://www.instagram.com/p/{0}/".format(media.code),
            url="https://www.instagram.com/web/likes/{0}/unlike/".format(
                media.id),
        )

        # Parsing
        try:
            if response.json()['status'] == 'ok':
                return True
            else:
                return False
        except (ValueError, KeyError):
            raise UnexpectedResponse(response.url, response.text)

    @Agent.exceptionDecorator
    def addComment(self, media, text, settings={}):
        # Check data
        if not isinstance(media, Media):
            raise TypeError("'media' must be Media type")
        if not isinstance(text, str):
            raise TypeError("'text' must be str type")
        if not isinstance(settings, dict):
            raise TypeError("'settings' must be dict type")
        if media.id == None:
            raise NotUpdatedElement(media, 'id')

        # Send request
        response = self.__action_handler__(
            referer="https://www.instagram.com/p/{0}/".format(media.code),
            url="https://www.instagram.com/web/comments/{0}/add/".format(
                media.id),
            data={'comment_text': text},
        )

        # Parsing
        try:
            data = response.json()
            if data['status'] == 'ok':
                comment = Comment(
                    id=data['id'],
                    media=media,
                    owner=self,
                    text=data['text'],
                    data=data['created_time'],
                )
                return comment
            return None
        except (ValueError, KeyError):
            raise UnexpectedResponse(response.url, response.text)

    @Agent.exceptionDecorator
    def deleteComment(self, comment, settings={}):
        # Check data
        if not isinstance(settings, dict):
            raise TypeError("'settings' must be dict type")
        if not isinstance(comment, Comment):
            raise TypeError("'comment' must be Comment type")
        if not comment.media == None:
            raise NotUpdatedElement(comment, 'media')
        if not comment.media.id == None:
            raise NotUpdatedElement(comment.media, 'id')

        # Send request
        response = self.__action_handler__(
            referer="https://www.instagram.com/p/{0}/".format(
                comment.media.code),
            url="https://www.instagram.com/web/comments/{0}/delete/{1}/".format(
                comment.media.id, comment.id),
        )

        # Parsing
        try:
            if response.json()['status'] == 'ok':
                del comment
                return True
            else:
                return False
        except (ValueError, KeyError):
            raise UnexpectedResponse(response.url, response.text)

    @Agent.exceptionDecorator
    def follow(self, account, settings={}):
        # Check data
        if not isinstance(settings, dict):
            raise TypeError("'settings' must be dict type")
        if not isinstance(account, Account):
            raise TypeError("'account' must be Account type")
        if account.id == None:
            raise NotUpdatedElement(account, 'id')

        # Send request
        response = self.__action_handler__(
            referer="https://www.instagram.com/{0}".format(account.login),
            url="https://www.instagram.com/web/friendships/{0}/follow/".format(
                account.id),
        )

        # Parsing
        try:
            if response.json()['status'] == 'ok':
                return True
            else:
                return False
        except (ValueError, KeyError):
            raise UnexpectedResponse(response.url, response.text)

    @Agent.exceptionDecorator
    def unfollow(self, account, settings={}):
        # Check data
        if not isinstance(settings, dict):
            raise TypeError("'settings' must be dict type")
        if not isinstance(account, Account):
            raise TypeError("'account' must be Account type")
        if account.id == None:
            raise NotUpdatedElement(account, 'id')

        # Send request
        response = self.__action_handler__(
            referer="https://www.instagram.com/{0}".format(account.login),
            url="https://www.instagram.com/web/friendships/{0}/unfollow/".format(
                account.id),
        )

        # Parsing
        try:
            if response.json()['status'] == 'ok':
                return True
            else:
                return False
        except (ValueError, KeyError):
            raise UnexpectedResponse(response.url, response.text)

    def __action_handler__(self, referer, url, data={}, settings={}):
        # Check data
        if not isinstance(settings, dict):
            raise TypeError("'settings' must be dict type")
        if not isinstance(data, dict):
            raise TypeError("'data' must be dict type")
        if not isinstance(referer, str):
            raise TypeError("'referer' must be str type")
        if not isinstance(url, str):
            raise TypeError("'url' must be str type")

        # Set data
        headers = {
            'referer': referer,
            'x-csrftoken': self.__session__.cookies['csrftoken'],
            'x-instagram-ajax': '1',
            'x-requested-with': 'XMLHttpRequest',
        }
        if 'headers' in settings:
            settings['headers'].update(headers)
        else:
            settings['headers'] = headers
        if 'data' in settings:
            settings['data'].update(data)
        else:
            settings['data'] = data

        # Send request
        response = self.__session__.post(url, **settings)
        return response


class Media:
    def __init__(self, code):
        self.id = None
        self.code = code
        self.caption = None
        self.owner = None
        self.date = None
        self.location = None
        self.likes_count = None
        self.comments_count = None
        self.comments_disabled = None
        self.is_video = None
        self.video_url = None
        self.is_ad = None
        self.display_url = None
        self.dimensions = None
        # Lists
        self.likes = set()
        self.comments = set()

    def __setDataFromJSON__(self, data):
        self.id = data['id']
        self.code = data['shortcode']
        if data['edge_media_to_caption']['edges']:
            self.caption = data['edge_media_to_caption']['edges'][0]['node'][
                'text']
        else:
            self.caption = None
        if 'username' in data['owner']:
            self.owner = Account(data['owner']['username'])
        self.date = data['taken_at_timestamp']
        if 'location' in data and data['location'] and 'id' in data['location']:
            self.location = Location(data['location']['id'])
        self.likes_count = data['edge_media_preview_like']['count']
        self.comments_count = data['edge_media_to_comment']['count']
        self.comments_disabled = data['comments_disabled']
        self.is_video = data['is_video']
        if self.is_video and 'video_url' in data:
            self.video_url = data['video_url']
        if 'is_ad' in data:
            self.is_ad = data['is_ad']
        self.display_url = data['display_url']


class Location:
    def __init__(self, id):
        self.id = id
        self.slug = None
        self.name = None
        self.has_public_page = None
        self.directory = None
        self.coordinates = None
        self.media_count = None
        # Lists
        self.media = set()
        self.top_posts = set()

    def __setDataFromJSON__(self, data):
        self.id = data['id']
        self.slug = data['slug']
        self.name = data['name']
        self.has_public_page = data['has_public_page']
        if 'directory' in data:
            self.directory = data['directory']
        self.coordinates = (data['lat'], data['lng'])
        self.media_count = data['edge_location_to_media']['count']
        for node in data['edge_location_to_top_posts']['edges']:
            self.top_posts.add(Media(node['node']['shortcode']))


class Tag:
    def __init__(self, name):
        self.name = name
        self.media_count = None
        # Lists
        self.media = set()
        self.top_posts = set()

    def __setDataFromJSON__(self, data):
        self.name = data['name']
        self.media_count = data['edge_hashtag_to_media']['count']
        for node in data['edge_hashtag_to_top_posts']['edges']:
            self.top_posts.add(Media(node['node']['shortcode']))


class Comment:
    def __init__(self, id, media, owner, text, created_at):
        self.id = id
        self.media = media
        self.owner = owner
        self.text = text
        self.created_at = created_at
