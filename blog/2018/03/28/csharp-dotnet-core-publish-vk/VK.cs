using System;
using System.Net.Http;
using System.Threading.Tasks;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;
using System.Text;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using System.Net;
using Microsoft.AspNetCore.Hosting;
using System.IO;

namespace protvshows.Code
{
    public class VK
    {
        private readonly IConfiguration _configuration;
        private readonly IWebHostEnvironment _hostingEnvironment;

        private static NLog.Logger _logger = NLog.LogManager.GetCurrentClassLogger();

        private readonly string _API,
                        _APIver,
                        _AccessToken,
                        _APImethodPost,
                        _APImethodGetWallUploadServer,
                        _APImethodSaveWallPhoto,
                        _PageID,
                        //
                        _getWallUploadServerURL,
                        _saveWallPhotoURL,
                        _postToWallURL;

        public VK(
            IConfiguration configuration,
            IWebHostEnvironment hostingEnvironment
            )
        {
            _hostingEnvironment = hostingEnvironment;
            _configuration = configuration;

            _API = _configuration.GetSection("VK:API").Value;
            _APIver = _configuration.GetSection("VK:APIver").Value;
            _AccessToken = _configuration.GetSection("VK:AccessToken").Value;
            _APImethodPost = _configuration.GetSection("VK:APImethodPost").Value;
            _APImethodGetWallUploadServer = _configuration.GetSection("VK:APImethotGetWallUploadServer").Value;
            _APImethodSaveWallPhoto = _configuration.GetSection("VK:APImethodSaveWallPhoto").Value;
            _PageID = _configuration.GetSection("VK:PageID").Value;

            _getWallUploadServerURL = $"{_API}{_APImethodGetWallUploadServer}?v={_APIver}&group_id={_PageID}&access_token={_AccessToken}";
            _saveWallPhotoURL = $"{_API}{_APImethodSaveWallPhoto}?v={_APIver}&group_id={_PageID}&access_token={_AccessToken}";
            _postToWallURL = $"{_API}{_APImethodPost}?v={_APIver}&owner_id=-{_PageID}&access_token={_AccessToken}";
        }

        public string PublishToVK(PostToPublish post)
        {
            try
            {
                // prepare the post
                StringBuilder postText = new StringBuilder()
                    .Append($"{post.Title} ({post.TitleRu}){Environment.NewLine}")
                    .Append(post.Description).Append(Environment.NewLine)
                    .Append($"{_configuration.GetSection("General:Domain").Value}ratings/{post.ShortName}")
                        .Append(Environment.NewLine)
                    .Append(post.Tags);

                string photoID = string.Empty;
                try // upload image
                {
                    string pathToImage = Path.Combine(
                        _hostingEnvironment.WebRootPath,
                        _configuration.GetSection("General:ImagesFolder").Value,
                        post.Picture
                        );
                    var rez = Task.Run(async () =>
                    {
                        var response = await UploadImage(pathToImage);
                        return response;
                    });
                    var rezJson = JObject.Parse(rez.Result.Item2);

                    if (rez.Result.Item1 != 200)
                    {
                        try // return error from JSON
                        {
                            return $"Error uploading photo to VK. {rezJson["error"][0]["error_msg"].Value<string>()}";
                        }
                        catch (Exception ex) // return unknown error
                        {
                            _logger.Error($"Unknown error uploading photo to VK. {General.PrepareExceptionMessage(ex, true)}");
                            return $"Unknown error uploading photo to VK. {ex.Message}";
                        }
                    }
                    // thank you, VK API, for that we need to check error even with 200 status
                    string errorPhoto = rezJson["error"] == null ? "OK" : rezJson["error"][0]["error_msg"].Value<string>();
                    if (errorPhoto != "OK")
                    {
                        return $"Error uploading photo to VK. {rezJson["error"][0]["error_msg"].Value<string>()}";
                    }

                    photoID = $"photo{rezJson["response"][0]["owner_id"].Value<string>()}_{rezJson["response"][0]["id"].Value<string>()}";
                }
                catch (Exception ex)
                {
                    _logger.Error($"Error uploading photo to VK. {General.PrepareExceptionMessage(ex, true)}");
                    return $"Error uploading photo to VK. {ex.Message}";
                }

                // publish a post with uploaded image
                var rezPost = Task.Run(async () =>
                {
                    return await WallPostWithPhoto(postText.ToString(), photoID);
                });
                var rezPostJson = JObject.Parse(rezPost.Result.Item2);

                if (rezPost.Result.Item1 != 200)
                {
                    try // return error from JSON
                    {
                        return $"Error posting to VK. {rezPostJson["error"][0]["error_msg"].Value<string>()}";
                    }
                    catch (Exception ex) // return unknown error
                    {
                        _logger.Error($"Unknown error posting to VK. {General.PrepareExceptionMessage(ex, true)}");
                        return $"Unknown error posting to VK. {ex.Message}";
                    }
                }
                // thank you, VK API, for that we need to check error even with 200 status
                string errorPost = rezPostJson["error"] == null ? "OK" : rezPostJson["error"][0]["error_msg"].Value<string>();
                if (errorPost != "OK")
                {
                    string errMsg = $"Error posting to VK. {rezPostJson["error"][0]["error_msg"].Value<string>()}";
                    _logger.Error(errMsg);
                    return errMsg;
                }

                return "OK";
            }
            catch (Exception ex)
            {
                _logger.Error(General.PrepareExceptionMessage(ex, true));
                return General.PrepareExceptionMessage(ex, false);
            }
        }

        public Task<Tuple<int, string>> UploadImage(string pathToImage)
        {
            // 1 - get upload server URL

            var serverRez = GetWallUploadServer();
            //Console.WriteLine(serverRez.Result.Item1);
            var serverRezJson = JObject.Parse(serverRez.Result.Item2);
            //Console.WriteLine(rezJson);
            bool error = serverRezJson["error"] != null;
            if (error)
            {
                string errorMsg = serverRezJson["error"]["error_msg"].Value<string>();
                throw new Exception(errorMsg);
            }
            string uploadServer = serverRezJson["response"]["upload_url"].Value<string>();
            //Console.WriteLine(server);

            // 2 - upload the image

            byte[] imgdata = System.IO.File.ReadAllBytes(pathToImage);
            var imageContent = new ByteArrayContent(imgdata);

            var multipartContent = new MultipartFormDataContent();
            multipartContent.Add(imageContent, "photo", "rating.png");

            var uploadRez = UploadPhoto(uploadServer, multipartContent);
            //Console.WriteLine(uploadRez.Result.Item1);
            var uploadRezJson = JObject.Parse(uploadRez.Result.Item2);
            //Console.WriteLine(uploadRezJson);
            // because empty node contains 2 chars
            bool isPhotoEmpty = uploadRezJson["photo"].Value<string>().Length < 3;
            if (isPhotoEmpty)
            {
                throw new Exception("VK API didn't like the request");
            }
            string server = uploadRezJson["server"].Value<string>();
            string photo = uploadRezJson["photo"].Value<string>();
            string hash = uploadRezJson["hash"].Value<string>();

            // 3 - save the uploaded image

            return SaveWallPhoto(server, photo, hash);
        }

        async Task<Tuple<int, string>> GetWallUploadServer()
        {
            using (var httpClient = new HttpClient())
            {
                var httpResponse = await httpClient.GetAsync(_getWallUploadServerURL);
                var httpContent = await httpResponse.Content.ReadAsStringAsync();

                return new Tuple<int, string>(
                    (int)httpResponse.StatusCode,
                    httpContent
                    );
            }
        }

        async Task<Tuple<int, string>> UploadPhoto(string server, MultipartFormDataContent multipartContent)
        {
            using (var httpClient = new HttpClient())
            {
                var httpResponse = await httpClient.PostAsync(server, multipartContent);
                // because here VK returns 1251-encoded response
                //var httpContent = await httpResponse.Content.ReadAsStringAsync();
                var httpContent = await httpResponse.Content.ReadAsByteArrayAsync();
                string responseString = Encoding.GetEncoding(1251).GetString(
                    httpContent, 0, httpContent.Length
                    );

                //Console.WriteLine($"1251 - {responseString}");
                // byte[] bytes = Encoding.Default.GetBytes(responseString);
                // responseString = Encoding.UTF8.GetString(bytes);
                // Console.WriteLine($"UTF-8 - {responseString}");

                return new Tuple<int, string>(
                    (int)httpResponse.StatusCode,
                    //httpContent
                    responseString
                    );
            }
        }

        async Task<Tuple<int, string>> SaveWallPhoto(string server, string photo, string hash)
        {
            using (var httpClient = new HttpClient())
            {
                var httpResponse = await httpClient.GetAsync(
                    $"{_saveWallPhotoURL}&server={server}&photo={photo}&hash={hash}"
                );
                var httpContent = await httpResponse.Content.ReadAsStringAsync();

                return new Tuple<int, string>(
                    (int)httpResponse.StatusCode,
                    httpContent
                    );
            }
        }

        public Task<Tuple<int, string>> WallPostWithPhoto(string post, string photoID)
        {
            var postData = new Dictionary<string, string> {
                    { "v", _APIver },
                    { "owner_id", $"-{_PageID}" },
                    { "access_token", _AccessToken},
                    { "from_group", "1" },
                    { "message", post },
                    { "attachments", photoID }
                };
            return PostToWall(postData);
        }

        async Task<Tuple<int, string>> PostToWall(Dictionary<string, string> postData)
        {
            using (var httpClient = new HttpClient())
            {
                var httpResponse = await httpClient.PostAsync(
                    $"{_API}{_APImethodPost}",
                    new FormUrlEncodedContent(postData)
                );
                var httpContent = await httpResponse.Content.ReadAsStringAsync();

                //_logger.LogWarning($"Result for PostToWall: {httpContent}");

                return new Tuple<int, string>(
                    (int)httpResponse.StatusCode,
                    httpContent
                    );
            }
        }

        public class PostToPublish
        {
            public string Picture { get; set; }
            public string Title { get; set; }
            public string TitleRu { get; set; }
            public string Description { get; set; }
            public string ShortName { get; set; }
            public string Tags { get; set; }
        }
    }
}
