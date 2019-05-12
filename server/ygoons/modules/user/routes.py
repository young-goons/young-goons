import random
import json
import sys
import os

import flask
from flask import request, jsonify, make_response, send_from_directory
from flask import current_app as app
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.utils import secure_filename

from ygoons.modules.user import blueprint, helpers, feed, svd


# TODO: distinguish public and private info
@blueprint.route('/user/<int:user_id>/info', methods=['GET'])
@jwt_required
def get_user_info(user_id):
    """
    Fetches and returns user information stored in tbl_user_info
    :param user_id: user_id of request
    """
    # check if the identity of the token is equal to the identity of the request parameter
    if user_id == get_jwt_identity()['userId']:
        user = helpers.get_user_data(user_id)
        if user is None:
            return make_response(
                jsonify({'msg': 'Error fetching user info data'}), 400)
        else:
            return make_response(jsonify({'user': user}), 200)
    else:
        return make_response(
            jsonify({'msg': 'No authentication on requested data'}), 400)


@blueprint.route('/user/id/username/<string:username>', methods=['GET'])
def get_user_exists_by_username(username):
    """
    Fetches existence of user of the input username and returns user_id if exists
    :param username: username to check existence of
    """

    with flask.g.pymysql_db.cursor() as cursor:
        sql = 'SELECT user_id FROM tbl_user WHERE username = %s'
        cursor.execute(sql, username)
        query_result = cursor.fetchone()
    if query_result is not None:
        return make_response(jsonify({'userId': query_result[0]}), 200)
    else:
        return make_response(jsonify({'userId': None}), 200)


@blueprint.route('/user/id/email/<string:email>', methods=['GET'])
def get_user_exists_by_email(email):
    """
    Fetches existence of user of the input email and returns username if exists
    :param email: email to check existence of
    """
    with flask.g.pymysql_db.cursor() as cursor:
        sql = 'SELECT user_id FROM tbl_user WHERE email = %s'
        cursor.execute(sql, email)
        query_result = cursor.fetchone()
    print(query_result)
    if query_result is not None:
        return make_response(jsonify({'userId': query_result[0]}), 200)
    else:
        return make_response(jsonify({'userId': None}), 200)


@blueprint.route('/user/<int:user_id>/posts', methods=['GET'])
@jwt_required
def get_user_posts(user_id):
    """ Obtains the list of ids of the posts that the user has posted """
    shuffle = request.args.get('shuffle')
    with flask.g.pymysql_db.cursor() as cursor:
        sql = 'SELECT post_id FROM tbl_post NATURAL JOIN ' \
              '(SELECT user_id, username FROM tbl_user WHERE user_id = %s) tbl_user_id'
        cursor.execute(sql, (user_id))
        query_result = cursor.fetchall()
    post_id_list = []
    for row in query_result:
        post_id_list.append(row[0])
    if shuffle:
        random.shuffle(post_id_list)
    return make_response(jsonify({'postIdArr': post_id_list}), 200)


@blueprint.route('/user/feed', methods=['GET'])
@jwt_required
def get_user_feed():
    """ Obtains the list of post_ids to appear on the user feed
        and sends the list of ids to the client """

    user_id = get_jwt_identity()['userId']
    with flask.g.pymysql_db.cursor() as cursor:
        # obtain the list of ids of the top posts shared by other users for now...
        # TODO: is this limit reasonable
        sql = '''SELECT tbl_post.post_id, like_cnt
        FROM tbl_post LEFT JOIN view_like_count
        ON tbl_post.post_id = view_like_count.post_id
        WHERE user_id != %s
        ORDER BY like_cnt DESC
        LIMIT 1000'''
        cursor.execute(sql, (user_id))
        top_posts = cursor.fetchall()

        # obtain list of ids of top posts shared by followed users
        sql = '''SELECT tbl_post.post_id, like_cnt
        FROM tbl_post LEFT JOIN view_like_count
        ON tbl_post.post_id = view_like_count.post_id
        WHERE user_id in
            (SELECT followed_id FROM tbl_follow WHERE follower_id = %s)
        ORDER BY like_cnt DESC
        LIMIT 1000'''
        cursor.execute(sql, (user_id))
        friend_posts = cursor.fetchall()

    cand_id_list = feed.select_feed_posts(friend_posts=friend_posts,
                                          top_posts=top_posts,
                                          limit=1000)

    # Reorder using SVD features
    clf = svd.SVD()
    clf.db_load_user([user_id])
    clf.db_load_post(cand_id_list)

    post_id_list = clf.get_recommendations(user_id,
                                           k=1000,
                                           candidates=cand_id_list)

    # TODO: introduce shuffling in top posts
    return make_response(jsonify({'postIdArr': post_id_list}), 200)


# TODO: error-handling for insert? make sure insert is actually executed
#       disable following yourself
@blueprint.route('/user/follow/<int:followed_user_id>', methods=['POST'])
@jwt_required
def user_follow(followed_user_id):
    """
    Follow the user of given user_id
    :param followed_user_id: user_id of the user to follow
    """
    curr_user = get_jwt_identity()
    curr_user_id = curr_user['userId']
    with flask.g.pymysql_db.cursor() as cursor:
        sql = 'INSERT INTO tbl_follow (followed_id, follower_id) ' \
              'VALUES (%s, %s)'
        row_cnt = cursor.execute(sql, (followed_user_id, curr_user_id))
    if row_cnt == 1:
        flask.g.pymysql_db.commit()
        return make_response(jsonify({'success': True}), 200)
    else:
        return make_response(jsonify({'msg': "User follow failed"}), 400)


# TODO: disabel unfollowing yourself
@blueprint.route('/user/follow/<int:followed_user_id>', methods=['DELETE'])
@jwt_required
def user_unfollow(followed_user_id):
    """
    Unfollow the user of given user_id
    :param followed_user_id: user_id of the user to unfollow
    :return:
    """
    curr_user = get_jwt_identity()
    curr_user_id = curr_user['userId']
    with flask.g.pymysql_db.cursor() as cursor:
        sql = 'DELETE FROM tbl_follow WHERE followed_id = %s AND follower_id = %s'
        affected_row_cnt = cursor.execute(sql,
                                          (followed_user_id, curr_user_id))
    if affected_row_cnt == 1:
        flask.g.pymysql_db.commit()
        return make_response(jsonify({'success': True}), 200)
    else:
        return make_response(jsonify({'msg': "User unfollow failed"}), 400)


@blueprint.route('/user/<int:user_id>/following', methods=['GET'])
def get_following(user_id):
    """
    Obtain the list of ids of the users that the user of user_id is following
    :param user_id:
    """
    with flask.g.pymysql_db.cursor() as cursor:
        sql = 'SELECT followed_id, username FROM ' \
              '(SELECT followed_id FROM tbl_follow WHERE follower_id = %s) tbl_user_follow ' \
              'INNER JOIN tbl_user ON followed_id = user_id'
        cursor.execute(sql, (user_id, ))
        query_result = cursor.fetchall()
    following_list = []
    for row in query_result:
        following_list.append(row[0])
    return make_response(jsonify({'followingArr': following_list}), 200)


@blueprint.route('/user/<int:user_id>/followers', methods=['GET'])
@jwt_required
def get_followers(user_id):
    """
    Obtain the list of ids of the users that follow the user of user_id
    :param user_id:
    """
    with flask.g.pymysql_db.cursor() as cursor:
        sql = 'SELECT follower_id, username FROM ' \
              '(SELECT follower_id FROM tbl_follow WHERE followed_id = %s) tbl_user_follow ' \
              'INNER JOIN tbl_user ON follower_id = user_id'
        cursor.execute(sql, (user_id, ))
        query_result = cursor.fetchall()
    follower_list = []
    for row in query_result:
        follower_list.append(row[0])
    return make_response(jsonify({'followerArr': follower_list}), 200)


@blueprint.route('/user/history/played/<int:post_id>', methods=['POST'])
@jwt_required
def upload_play_history(post_id):
    user_id = get_jwt_identity()['userId']
    with flask.g.pymysql_db.cursor() as cursor:
        sql = 'INSERT INTO tbl_play_history (user_id, post_id)' \
              'VALUES (%s, %s)'
        row_cnt = cursor.execute(sql, (user_id, post_id))
    if row_cnt == 1:
        flask.g.pymysql_db.commit()
        return make_response(jsonify({'success': True}), 200)
    else:
        return make_response(jsonify({'success': False}), 400)


@blueprint.route('/user/<int:user_id>/profile/image', methods=['GET'])
def get_user_profile_image(user_id):
    query_result = helpers.get_profile_picture_path(user_id)
    if query_result is not None:
        file_name = query_result.split('/')[-1]
        file_path = '/'.join(query_result.split('/')[:-1])
        return send_from_directory(file_path, file_name, mimetype='image/jpeg')
    else:
        return make_response(jsonify({'msg': "user_id is not found"}), 400)


@blueprint.route('/user/<int:user_id>/profile/image', methods=['POST'])
@jwt_required
def upload_user_profile_image(user_id):
    curr_user_id = get_jwt_identity()['userId']
    if user_id != curr_user_id:
        return make_response(jsonify({'msg': "Authorization failed", 'success': False}), 400)

    image = request.files['file']

    if not os.path.isdir(
            os.path.join(app.config["IMAGE_STORAGE_PATH"], str(user_id))):
        os.makedirs(os.path.join(app.config["IMAGE_STORAGE_PATH"], str(user_id)),
                    exist_ok=True)
    image_file_path = os.path.join(app.config['IMAGE_STORAGE_PATH'],
                                   str(user_id), secure_filename(image.filename))
    if os.path.exists(image_file_path):
        image.save(image_file_path)

    row_cnt = helpers.upload_profile_picture(user_id, image_file_path)
    if row_cnt == 1:
        flask.g.pymysql_db.commit()
    return make_response(jsonify({'success': True}), 200)


@blueprint.route('/user/<int:user_id>/header/image', methods=['GET'])
def get_user_header_image(user_id):
    query_result = helpers.get_header_picture_path(user_id)
    print(query_result)
    if query_result is not None:
        file_name = query_result.split('/')[-1]
        file_path = '/'.join(query_result.split('/')[:-1])
        return send_from_directory(file_path, file_name, mimetype='image/jpeg')
    else:
        return make_response(jsonify({'msg': "user_id is not found"}), 400)


@blueprint.route('/user/<int:user_id>/header/image', methods=['POST'])
@jwt_required
def upload_user_header_image(user_id):
    curr_user_id = get_jwt_identity()['userId']
    if user_id != curr_user_id:
        return make_response(jsonify({'msg': "Authorization failed", 'success': False}), 400)

    image = request.files['file']

    if not os.path.isdir(
            os.path.join(app.config["IMAGE_STORAGE_PATH"], str(user_id))):
        os.makedirs(os.path.join(app.config["IMAGE_STORAGE_PATH"], str(user_id)),
                    exist_ok=True)
    image_file_path = os.path.join(app.config['IMAGE_STORAGE_PATH'],
                                   str(user_id), secure_filename(image.filename))
    image.save(image_file_path)

    row_cnt = helpers.upload_header_picture(user_id, image_file_path)
    if row_cnt == 1:
        flask.g.pymysql_db.commit()
    return make_response(jsonify({'success': True}), 200)
