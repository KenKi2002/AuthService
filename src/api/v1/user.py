from http import HTTPStatus

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from api.v1.components.role_schemas import Role as RoleSchem
from api.v1.components.user_schemas import Session as SessionSchem
from api.v1.utils import Pagination, check_permission
from core.config import settings
from user.payload_models import (
    ChangePasswordPayload,
    LogoutPayload,
    OAuthUser,
    RefreshTokensPayload,
    UserCreatePayload,
    UserID,
    UserLoginPayload,
)
from user.services import OAuthService, UserAuthService, UserService
from utils.exceptions import EmailAlreadyExist, InvalidPassword, NoAccessError, NotFoundError, UniqueConstraintError

auth_blueprint = Blueprint('auth', __name__, url_prefix='/api/v1/auth')
user_blueprint = Blueprint('user', __name__, url_prefix='/api/v1/user')

auth_service = UserAuthService()
user_service = UserService()


@auth_blueprint.route('/register', methods=('POST',))
def register():
    """
    Регистрация нового пользователя.
    ---
    post:
     summary: Регистрация нового пользователя
     requestBody:
       content:
        application/json:
         schema: Register
     responses:
       '200':
         description: New user was registered
       '409':
         description: Email is already in use
     tags:
       - Auth
    """
    _request = {
        'username': request.json.get('username'),
        'email': request.json.get('email'),
        'password': request.json.get('password'),
    }
    try:
        auth_service.register(UserCreatePayload(**_request))
    except EmailAlreadyExist:
        return jsonify(message='Email is already in use'), HTTPStatus.CONFLICT
    return jsonify(message='New user was registered'), HTTPStatus.OK


@auth_blueprint.route('/register/<provider>', methods=('POST',))
def oauth_register(provider):
    """
    Вход пользователя в аккаунт.
    ---
    post:
     summary: Вход пользователя в аккаунт
     responses:
       '200':
         description: Register successful
       '404':
         description: Unsupportable provider
     tags:
       - Auth
    """
    if not settings.oauth.providers.has_value(provider):
        return jsonify(message='Unsupportable provider.'), HTTPStatus.NOT_FOUND
    oauth = OAuthService.get_provider(provider)
    return oauth.authorize('oauth_register_callback')


@auth_blueprint.route('/register-callback/<provider>', methods=('GET',))
def oauth_register_callback(provider):
    """
    Вход пользователя в аккаунт.
    ---
    post:
     summary: Вход пользователя в аккаунт
     responses:
       '200':
         description: Login successful
       '403':
         description: Permission denied
       '409':
         description: Email is already in use or social account is already linked to another user
     tags:
       - Auth
    """
    code = request.args.get('code', default=None)
    oauth = OAuthService.get_provider(provider)
    try:
        user_social_data = oauth.callback(code)
    except NoAccessError:
        return jsonify(message='Authentication failed.'), HTTPStatus.FORBIDDEN
    try:
        user_data = OAuthUser(user_agent=request.headers.get('User-Agent'), **user_social_data.dict())
        oauth.register(user_data)
    except EmailAlreadyExist:
        return jsonify(message='Email is already in use'), HTTPStatus.CONFLICT
    except UniqueConstraintError:
        return jsonify(message='Social account is already linked'), HTTPStatus.CONFLICT

    return jsonify(message='New user was registered'), HTTPStatus.OK


@auth_blueprint.route('/login', methods=('POST',))
def login():
    """
    Вход пользователя в аккаунт.
    ---
    post:
     summary: Вход пользователя в аккаунт
     requestBody:
       content:
        application/json:
         schema: Login
     responses:
       '200':
         description: Login successful
       '400':
         description: Wrong password
       '401':
         description: User is not exist
       '403':
         description: Permission denied
     tags:
       - Auth
    """
    _request = {
        'email': request.json.get('email'),
        'password': request.json.get('password'),
        'user_agent': request.headers.get('User-Agent'),
    }
    try:
        access_token, refresh_token = auth_service.login(UserLoginPayload(**_request))
    except NotFoundError:
        return jsonify(message='User is not exist'), HTTPStatus.UNAUTHORIZED
    except InvalidPassword:
        return jsonify(message='Wrong password'), HTTPStatus.BAD_REQUEST

    response = jsonify(
        message='Login successful',
        tokens={
            'access_token': access_token,
            'refresh_token': refresh_token,
        },
    )
    return response, HTTPStatus.OK


@auth_blueprint.route('/login/<provider>', methods=('POST',))
def oauth_login(provider):
    """
    Вход пользователя в аккаунт.
    ---
    post:
     summary: Вход пользователя в аккаунт
     responses:
       '200':
         description: Login successful
       '404':
         description: Unsupportable provider
     tags:
       - Auth
    """
    if not settings.oauth.providers.has_value(provider):
        return jsonify(message='Unsupportable provider.'), HTTPStatus.NOT_FOUND
    oauth = OAuthService.get_provider(provider)
    return oauth.authorize('oauth_login_callback')


@auth_blueprint.route('/login-callback/<provider>', methods=('GET',))
def oauth_login_callback(provider):
    """
    Вход пользователя в аккаунт.
    ---
    post:
     summary: Вход пользователя в аккаунт
     responses:
       '200':
         description: Login successful
       '403':
         description: Authentication failed
       '404':
         description: User didnt exist or social account didnt linked to user
     tags:
       - Auth
    """
    code = request.args.get('code', default=None)
    oauth = OAuthService.get_provider(provider)
    try:
        user_social_data = oauth.callback(code)
    except NoAccessError:
        return jsonify(message='Authentication failed.'), HTTPStatus.FORBIDDEN
    try:
        user_data = OAuthUser(user_agent=request.headers.get('User-Agent'), **user_social_data.dict())
        access_token, refresh_token = oauth.login(user_data)
    except NotFoundError:
        return jsonify(message='User didnt exist or social account didnt linked to user'), HTTPStatus.NOT_FOUND
    response = jsonify(
        message='Login successful',
        tokens={
            'access_token': access_token,
            'refresh_token': refresh_token,
        },
    )
    return response, HTTPStatus.OK


@auth_blueprint.route('/change-password', methods=('PATCH',))
@jwt_required()
@check_permission(permission=settings.permission.User)
def change_password():
    """
    Смена пароля.
    ---
    patch:
     security:
      - AccessAuth: []
     summary: Смена пароля
     parameters:
      - name: user_id
        in: path
        type: string
        required: true
     requestBody:
       content:
        application/json:
         schema: ChangePassword
     responses:
       '200':
         description: Password changed successful
       '400':
         description: Wrong password
       '401':
         description: User is not exist
       '403':
         description: Permission denied
     tags:
       - Auth
    """

    _request = {
        'user_id': get_jwt_identity(),
        'old_password': request.json.get('old_password'),
        'new_password': request.json.get('new_password'),
    }
    try:
        user_service.change_password(ChangePasswordPayload(**_request))
    except NotFoundError:
        return jsonify(message='User is not exist'), HTTPStatus.UNAUTHORIZED
    except InvalidPassword:
        return jsonify(message='Wrong password'), HTTPStatus.BAD_REQUEST
    return jsonify(message='Password changed successful'), HTTPStatus.OK


@auth_blueprint.route('/refresh-token', methods=('POST',))
@jwt_required(refresh=True)
def refresh_tokens():
    """
    Обновление токенов.
    ---
    post:
     security:
      - RefreshAuth: []
     summary: Обновление токенов
     responses:
       '200':
         description: Refresh successful
       '400':
         description: Not user
     tags:
       - Auth
    """

    _request = {
        'user_id': get_jwt_identity(),
        'user_agent': request.headers.get('User-Agent'),
        'refresh': request.headers.get('Authorization')[7:],
    }
    try:
        access_token, refresh_token = auth_service.refresh_tokens(RefreshTokensPayload(**_request))
    except NoAccessError:
        return jsonify(message='Not user'), HTTPStatus.BAD_REQUEST
    except NotFoundError:
        return jsonify(message='User is not exist'), HTTPStatus.UNAUTHORIZED
    response = jsonify(
        message='Refresh successful',
        tokens={
            'access_token': access_token,
            'refresh_token': refresh_token,
        },
    )
    return response, HTTPStatus.OK


@auth_blueprint.route('/logout', methods=('POST',))
@jwt_required()
def logout():
    """
    Выход пользователя из аккаунта.
    ---
    post:
     security:
      - AccessAuth: []
     summary: Выход пользователя из аккаунта
     requestBody:
       content:
        application/json:
         schema: Logout
     responses:
       '200':
         description: User logged out
       '400':
         description: Not user
     tags:
       - Auth
    """
    _request = {
        'user_id': get_jwt_identity(),
        'user_agent': request.headers.get('User-Agent'),
        'from_all': request.json.get('from_all'),
    }
    try:
        auth_service.logout(LogoutPayload(**_request))
    except NotFoundError:
        return jsonify(message='Not user'), HTTPStatus.UNAUTHORIZED

    return jsonify(message='User logged out'), HTTPStatus.OK


@user_blueprint.route('/login-history/<uuid:user_id>', methods=('GET',))
@check_permission(permission=settings.permission.User)
def login_history(user_id):
    """
    Получить историю посещений.
    ---
    get:
     security:
      - AccessAuth: []
     summary: Получить историю посещений
     parameters:
      - name: user_id
        in: path
        type: string
        required: true
      - name: page
        in: query
        type: integer
        required: False
      - name: size
        in: query
        type: integer
        required: False
     responses:
       '200':
         description: User logged out
       '204':
         description: No content
       '400':
         description: Not user
       '403':
         description: Permission denied
     tags:
       - User
    """

    _request = {
        'user_id': user_id,
    }
    args = request.args
    paginate = Pagination(page=args.get('page', type=int), size=args.get('size', type=int))
    try:
        user_histories = user_service.get_history(UserID(**_request), paginate)
    except NoAccessError:
        return jsonify(message='Not user'), HTTPStatus.BAD_REQUEST
    if not user_histories:
        return '', HTTPStatus.NO_CONTENT
    return (
        jsonify(history=[SessionSchem().dumps(history) for history in user_histories]),
        HTTPStatus.OK,
    )


@user_blueprint.route(
    '/roles/<uuid:user_id>',
    methods=(
        'GET',
        'POST',
        'DELETE',
    ),
)
@jwt_required()
@check_permission(permission=settings.permission.Moderator)
def user_roles(user_id):  # noqa: C901
    """
    Получение | Добавление | Удаление ролей пользователя.
    ---
    get:
     security:
      - AccessAuth: []
     summary: Получение списка всех ролей пользователя
     parameters:
      - name: user_id
        in: path
        type: string
        required: true
     responses:
       '200':
         description: Ok
       '204':
         description: Role list is empty
       '403':
         description: Permission denied
       '404':
         description: User not found
     tags:
       - User
    post:
     security:
      - AccessAuth: []
     summary: Добавление роли пользователю
     parameters:
      - name: user_id
        in: path
        type: string
        required: true
      - name: role_id
        in: query
        type: string
        required: true
     responses:
       '200':
         description: Role assigned to user
       '204':
         description: Role list is empty
       '403':
         description: Permission denied
       '409':
         description: Role is already in use
     tags:
       - User
    delete:
     security:
      - AccessAuth: []
     summary: Удаление роли у пользователя
     parameters:
      - name: user_id
        in: path
        type: string
        required: true
      - name: role_id
        in: query
        type: string
        required: true
     responses:
       '200':
         description: Ok
       '204':
         description: Role list is empty
       '403':
         description: Permission denied
       '409':
         description: Role is already deleted
     tags:
       - User
    """
    if request.method == 'GET':
        _request = {
            'user_id': user_id,
        }
        try:
            roles = user_service.get_roles(**_request)
        except NotFoundError:
            return jsonify(message='User not found'), HTTPStatus.NOT_FOUND
        if not roles:
            return jsonify(message='Role list is empty'), HTTPStatus.NO_CONTENT
        return (
            jsonify(roles=[RoleSchem().dumps(role) for role in roles]),
            HTTPStatus.OK,
        )

    if request.method == 'POST':
        _request = {
            'user_id': user_id,
            'role_id': request.args.get('role_id'),
        }
        try:
            user_service.add_role(**_request)
        except NotFoundError:
            return jsonify(message='Not found'), HTTPStatus.NOT_FOUND
        except UniqueConstraintError:
            return jsonify(message='Role is already in use'), HTTPStatus.CONFLICT
        return jsonify(message='Role assigned to user'), HTTPStatus.OK

    if request.method == 'DELETE':
        _request = {
            'user_id': user_id,
            'role_id': request.args.get('role_id'),
        }
        try:
            user_service.remove_role(**_request)
        except NotFoundError:
            return jsonify(message='Not found'), HTTPStatus.NOT_FOUND
        return jsonify(message='Role was deleted sucessfully'), HTTPStatus.OK
